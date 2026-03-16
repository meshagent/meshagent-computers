import asyncio
import contextlib
import errno
import json
import logging
import os
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import Browser, Error as PlaywrightError, Page

from meshagent.api import RoomClient
from meshagent.api.port_forward import LocalExposeHandle, port_forward

from .base_playwright import BasePlaywrightComputer


logger = logging.getLogger("computer_use")
PLAYWRIGHT_CONTAINER_NAME = "playwright"
PLAYWRIGHT_REMOTE_PORT = 3000
PLAYWRIGHT_CONNECT_TIMEOUT_SECONDS = 90.0
PLAYWRIGHT_CONNECT_ATTEMPT_TIMEOUT_SECONDS = 10.0
PLAYWRIGHT_CONNECT_BACKOFF_INITIAL_SECONDS = 0.25
PLAYWRIGHT_CONNECT_BACKOFF_MAX_SECONDS = 4.0
_PLAYWRIGHT_CONNECT_TIMEOUT_ENV_VAR = "MESHAGENT_PLAYWRIGHT_CONNECT_TIMEOUT_SECONDS"


def _resolve_playwright_connect_timeout_seconds() -> float:
    raw_timeout = os.getenv(_PLAYWRIGHT_CONNECT_TIMEOUT_ENV_VAR)
    if raw_timeout is None:
        return PLAYWRIGHT_CONNECT_TIMEOUT_SECONDS

    try:
        timeout = float(raw_timeout.strip())
    except ValueError:
        return PLAYWRIGHT_CONNECT_TIMEOUT_SECONDS

    if timeout <= 0:
        return PLAYWRIGHT_CONNECT_TIMEOUT_SECONDS

    return timeout


class ContainerPlaywrightComputer(BasePlaywrightComputer):
    """Launches a containerized Chromium instance using Playwright."""

    def __init__(
        self,
        *,
        headless: bool = False,
        image: str | None = None,
        room: RoomClient,
        env: dict[str, str] | None = None,
        dimensions: tuple[int, int] | None = None,
        starting_url: str | None = None,
    ):
        super().__init__(dimensions=dimensions, starting_url=starting_url)
        self.headless = headless
        self.image = image or "meshagent/playwright:default"
        self.container_name = PLAYWRIGHT_CONTAINER_NAME
        self.container_command = (
            "/bin/sh -c "
            f'"playwright run-server --port {PLAYWRIGHT_REMOTE_PORT} --host 0.0.0.0"'
        )
        self.room = room
        self.container_fut = None
        self._forwarder: LocalExposeHandle | None = None
        self.connect_timeout_seconds = _resolve_playwright_connect_timeout_seconds()
        self.connect_attempt_timeout_seconds = (
            PLAYWRIGHT_CONNECT_ATTEMPT_TIMEOUT_SECONDS
        )
        self.connect_backoff_initial_seconds = (
            PLAYWRIGHT_CONNECT_BACKOFF_INITIAL_SECONDS
        )
        self.connect_backoff_max_seconds = PLAYWRIGHT_CONNECT_BACKOFF_MAX_SECONDS
        self.env = env or {}

    async def _find_or_create_container(self):
        containers = await self.room.containers.list()

        for container in containers:
            if container.name != self.container_name:
                continue

            if container.state != "RUNNING":
                logger.info(
                    "playwright container not running, recreating (state=%s)",
                    container.state,
                )
                await self.room.containers.delete(container_id=container.id)
                break

            logger.info("playwright container found, using existing container")
            return container.id

        logger.info("playwright container not found, spinning up")
        return await self.room.containers.run(
            env=self.env,
            name=self.container_name,
            image=self.image,
            command=self.container_command,
            writable_root_fs=True,
            ports={3000: 3000},
        )

    async def ensure_container(self):
        if self.container_fut is None:
            self.container_fut = asyncio.ensure_future(self._find_or_create_container())

        return await self.container_fut

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            await super().__aexit__(exc_type, exc_val, exc_tb)
        finally:
            await self._close_forwarder()

    async def _close_forwarder(self) -> None:
        if self._forwarder is None:
            return
        forwarder = self._forwarder
        self._forwarder = None
        await forwarder.close()

    async def _base_url(self, *, container_id: str) -> str:
        if self._forwarder is None:
            logger.info("exposing local port forward for remote playwright container")
            self._forwarder = await port_forward(
                container_id=container_id,
                port=PLAYWRIGHT_REMOTE_PORT,
                token=self.room.protocol.token,
            )

        return f"ws://{self._forwarder.host}:{self._forwarder.port}/"

    @staticmethod
    def _is_version_mismatch_error(error: Exception) -> bool:
        return "playwright version mismatch" in str(error).lower()

    @staticmethod
    def _is_retryable_connect_error(error: Exception) -> bool:
        if isinstance(error, TimeoutError):
            return True

        if isinstance(error, ConnectionError):
            return True

        if isinstance(error, PlaywrightError):
            message = str(error).lower()
            return (
                "timed out" in message
                or "timeout" in message
                or "econnrefused" in message
                or "connection refused" in message
                or "socket hang up" in message
                or "econnreset" in message
                or "connection reset" in message
            )

        if isinstance(error, OSError):
            if error.errno in (errno.ECONNREFUSED, errno.ECONNRESET):
                return True

        message = str(error).lower()
        return (
            "timed out" in message
            or "timeout" in message
            or "econnrefused" in message
            or "connection refused" in message
            or "socket hang up" in message
            or "econnreset" in message
            or "connection reset" in message
        )

    async def _reset_after_connect_failure(self) -> None:
        await self._close_forwarder()
        await self.restart_playwright_client()

    @staticmethod
    def _health_check_target(*, base_url: str) -> tuple[str, int, str]:
        parsed = urlsplit(base_url)
        if parsed.hostname is None or parsed.port is None:
            raise ValueError(f"invalid playwright base url: {base_url!r}")
        path = "/json"
        return parsed.hostname, parsed.port, path

    @staticmethod
    def _health_check_url(*, base_url: str) -> str:
        parsed = urlsplit(base_url)
        return urlunsplit(("http", parsed.netloc, "/json", "", ""))

    @staticmethod
    def _ws_endpoint_url(*, base_url: str, ws_endpoint_path: str | None) -> str:
        parsed = urlsplit(base_url)
        path = ws_endpoint_path or parsed.path or "/"
        return urlunsplit(("ws", parsed.netloc, path, "", ""))

    async def _check_server_ready_once(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
    ) -> str:
        host, port, path = self._health_check_target(base_url=base_url)

        async def _probe() -> str:
            reader, writer = await asyncio.open_connection(host=host, port=port)
            try:
                request = (
                    f"GET {path} HTTP/1.1\r\n"
                    f"Host: {host}:{port}\r\n"
                    "Connection: close\r\n\r\n"
                )
                writer.write(request.encode("ascii"))
                await writer.drain()
                status_line = await reader.readline()
                if not status_line:
                    raise ConnectionError("playwright container is still starting")
                if status_line.startswith(b"HTTP/"):
                    parts = status_line.split(maxsplit=2)
                    if len(parts) >= 2:
                        try:
                            status_code = int(parts[1])
                        except ValueError:
                            status_code = None
                        if status_code is not None and status_code >= 500:
                            raise ConnectionError(
                                f"playwright health check returned HTTP {status_code}"
                            )
                while True:
                    line = await reader.readline()
                    if line in (b"", b"\r\n", b"\n"):
                        break
                body = await reader.read()
                try:
                    payload = json.loads(body.decode("utf-8"))
                except Exception:
                    payload = {}
                ws_endpoint_path = payload.get("wsEndpointPath")
                if ws_endpoint_path is not None and not isinstance(
                    ws_endpoint_path, str
                ):
                    ws_endpoint_path = None
                return self._ws_endpoint_url(
                    base_url=base_url,
                    ws_endpoint_path=ws_endpoint_path,
                )
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

        return await asyncio.wait_for(_probe(), timeout=timeout_seconds)

    async def _connect_browser_and_page_once(
        self,
        *,
        base_url: str,
        headers: dict[str, str],
        width: int,
        height: int,
        timeout_seconds: float,
    ) -> tuple[Browser, Page]:
        browser = await self._playwright.chromium.connect(
            base_url,
            headers=headers,
            timeout=timeout_seconds * 1000,
        )
        try:
            page = await asyncio.wait_for(
                browser.new_page(),
                timeout=timeout_seconds,
            )
            await page.set_viewport_size({"width": width, "height": height})
            return browser, page
        except asyncio.CancelledError:
            await browser.close()
            raise
        except Exception:
            await browser.close()
            raise

    async def _get_browser_and_page(self) -> tuple[Browser, Page]:
        container_id = await self.ensure_container()

        width, height = self.dimensions
        headers = {}
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.connect_timeout_seconds
        backoff_seconds = self.connect_backoff_initial_seconds
        attempt = 1

        try:
            while True:
                try:
                    base_url = await self._base_url(
                        container_id=container_id,
                    )
                    now = loop.time()
                    remaining_seconds = deadline - now
                    if remaining_seconds <= 0:
                        raise TimeoutError(
                            "timed out waiting for playwright websocket endpoint to become ready"
                        )

                    attempt_timeout_seconds = min(
                        self.connect_attempt_timeout_seconds,
                        remaining_seconds,
                    )
                    logger.info(
                        "checking playwright endpoint (attempt %s): %s",
                        attempt,
                        self._health_check_url(base_url=base_url),
                    )
                    ws_endpoint_url = await self._check_server_ready_once(
                        base_url=base_url,
                        timeout_seconds=attempt_timeout_seconds,
                    )
                    remaining_seconds = deadline - loop.time()
                    if remaining_seconds <= 0:
                        raise TimeoutError(
                            "timed out waiting for playwright websocket endpoint to become ready"
                        )
                    attempt_timeout_seconds = min(
                        self.connect_attempt_timeout_seconds,
                        remaining_seconds,
                    )
                    logger.info(
                        "connecting to playwright websocket (attempt %s): %s",
                        attempt,
                        ws_endpoint_url,
                    )
                    browser, page = await self._connect_browser_and_page_once(
                        base_url=ws_endpoint_url,
                        headers=headers,
                        width=width,
                        height=height,
                        timeout_seconds=attempt_timeout_seconds,
                    )
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    if self._is_version_mismatch_error(error):
                        raise

                    should_retry = self._is_retryable_connect_error(error)
                    if not should_retry:
                        raise

                    now = loop.time()
                    remaining_seconds = deadline - now
                    if remaining_seconds <= 0:
                        raise TimeoutError(
                            "timed out waiting for playwright websocket endpoint to become ready"
                        ) from error

                    delay_seconds = min(backoff_seconds, remaining_seconds)
                    reason = str(error).strip()
                    if not reason:
                        reason = "playwright browser session is still initializing"
                    if reason == "playwright container is still starting":
                        logger.info(
                            (
                                "playwright container is still starting "
                                "(attempt %s); checking again in %.2fs"
                            ),
                            attempt,
                            delay_seconds,
                        )
                    elif reason == "playwright browser session is still initializing":
                        await self._reset_after_connect_failure()
                        logger.info(
                            (
                                "playwright endpoint is up but the browser session "
                                "is still initializing (attempt %s); retrying in %.2fs"
                            ),
                            attempt,
                            delay_seconds,
                        )
                    else:
                        await self._reset_after_connect_failure()
                        logger.info(
                            (
                                "playwright startup attempt %s failed; "
                                "retrying in %.2fs (%s)"
                            ),
                            attempt,
                            delay_seconds,
                            reason,
                        )
                    logger.debug(
                        "playwright startup retryable error details",
                        exc_info=error,
                    )
                    await asyncio.sleep(delay_seconds)
                    backoff_seconds = min(
                        backoff_seconds * 2,
                        self.connect_backoff_max_seconds,
                    )
                    attempt += 1
        except asyncio.CancelledError:
            await self._close_forwarder()
            raise
        except Exception:
            await self._close_forwarder()
            raise

        logger.info("playwright ready")
        await page.goto(self.starting_url)
        return browser, page
