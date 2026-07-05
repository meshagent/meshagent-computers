import asyncio
import base64
import contextlib
import errno
import json
import logging
import os
from collections.abc import Awaitable
from typing import TypeVar
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import Browser, Error as PlaywrightError, Page

from meshagent.api import RoomClient
from meshagent.api.port_forward import LocalExposeHandle, port_forward
from meshagent.api.room_server_client import RoomContainer

from .base_playwright import BasePlaywrightComputer
from .computer import ComputerContext


logger = logging.getLogger("computer_use")
PLAYWRIGHT_CONTAINER_NAME = "playwright"
PLAYWRIGHT_REMOTE_PORT = 3000
PLAYWRIGHT_CONNECT_TIMEOUT_SECONDS: float | None = None
PLAYWRIGHT_CONNECT_ATTEMPT_TIMEOUT_SECONDS = 30.0
PLAYWRIGHT_CONNECT_BACKOFF_INITIAL_SECONDS = 0.25
PLAYWRIGHT_CONNECT_BACKOFF_MAX_SECONDS = 4.0
PLAYWRIGHT_FORWARDER_CLOSE_TIMEOUT_SECONDS = 5.0
_PLAYWRIGHT_CONNECT_TIMEOUT_ENV_VAR = "MESHAGENT_PLAYWRIGHT_CONNECT_TIMEOUT_SECONDS"
_WaitResult = TypeVar("_WaitResult")


def _resolve_playwright_connect_timeout_seconds() -> float | None:
    raw_timeout = os.getenv(_PLAYWRIGHT_CONNECT_TIMEOUT_ENV_VAR)
    if raw_timeout is None:
        return PLAYWRIGHT_CONNECT_TIMEOUT_SECONDS

    normalized = raw_timeout.strip().lower()
    if normalized in ("", "none", "off", "false"):
        return None

    try:
        timeout = float(raw_timeout.strip())
    except ValueError:
        return PLAYWRIGHT_CONNECT_TIMEOUT_SECONDS

    if timeout <= 0:
        return None

    return timeout


def _discard_task_result(task: asyncio.Task[object]) -> None:
    def _consume_result(done_task: asyncio.Task[object]) -> None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            done_task.result()

    if task.done():
        _consume_result(task)
        return
    task.add_done_callback(_consume_result)


async def _wait_with_timeout_without_waiting_for_cancellation(
    awaitable: Awaitable[_WaitResult],
    *,
    timeout_seconds: float,
    timeout_error_message: str,
) -> _WaitResult:
    task = asyncio.create_task(awaitable)
    done, _ = await asyncio.wait({task}, timeout=timeout_seconds)
    if task not in done:
        task.cancel()
        _discard_task_result(task)
        raise TimeoutError(timeout_error_message)
    return task.result()


async def _await_cleanup_without_waiting_for_cancellation(
    awaitable: Awaitable[object],
    *,
    timeout_seconds: float,
) -> None:
    task = asyncio.create_task(awaitable)
    done, _ = await asyncio.wait({task}, timeout=timeout_seconds)
    if task not in done:
        task.cancel()
        await asyncio.sleep(0)
        if task.done():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                task.result()
            return
        _discard_task_result(task)
        return
    with contextlib.suppress(asyncio.CancelledError, Exception):
        task.result()


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
        self.connect_timeout_seconds: float | None = (
            _resolve_playwright_connect_timeout_seconds()
        )
        self.connect_attempt_timeout_seconds = (
            PLAYWRIGHT_CONNECT_ATTEMPT_TIMEOUT_SECONDS
        )
        self.connect_backoff_initial_seconds = (
            PLAYWRIGHT_CONNECT_BACKOFF_INITIAL_SECONDS
        )
        self.connect_backoff_max_seconds = PLAYWRIGHT_CONNECT_BACKOFF_MAX_SECONDS
        self.env = env or {}

    async def _list_playwright_containers(self) -> list[RoomContainer]:
        containers = await self.room.containers.list()
        return [
            container
            for container in containers
            if container.name == self.container_name
        ]

    async def _run_container(self) -> str:
        logger.info("playwright container not found, spinning up")
        return await self.room.containers.run(
            env=self.env,
            name=self.container_name,
            image=self.image,
            command=self.container_command,
            writable_root_fs=True,
            ports={3000: 3000},
        )

    async def _cached_container_id(self) -> str | None:
        if self.container_fut is None:
            return None
        return await self.container_fut

    async def _cached_container_is_running(self) -> bool:
        container_id = await self._cached_container_id()
        if container_id is None:
            return False

        for container in await self._list_playwright_containers():
            if container.id != container_id:
                continue
            return container.state == "RUNNING"

        return False

    async def _find_or_create_container(self):
        for container in await self._list_playwright_containers():
            if container.state != "RUNNING":
                logger.info(
                    "playwright container not running, recreating (state=%s)",
                    container.state,
                )
                await self.room.containers.delete(container_id=container.id)
                break

            logger.info("playwright container found, using existing container")
            return container.id

        return await self._run_container()

    def _emit_startup_progress(
        self,
        *,
        context: ComputerContext | None,
        details: str,
    ) -> None:
        if context is None:
            return
        context.emit_startup(state="in_progress", details=(details,))

    async def ensure_container(self):
        return await self._ensure_container(context=None)

    async def _ensure_container(self, *, context: ComputerContext | None) -> str:
        if self.container_fut is None:
            self._emit_startup_progress(
                context=context,
                details="Starting Playwright container.",
            )
            self.container_fut = asyncio.ensure_future(self._run_container())
            return await self.container_fut

        if await self._cached_container_is_running():
            return await self.container_fut

        container_id = await self._cached_container_id()
        if container_id is not None:
            logger.info(
                "playwright container exited or disappeared, recreating (container_id=%s)",
                container_id,
            )
        self._emit_startup_progress(
            context=context,
            details="Restarting Playwright container.",
        )
        await self._reset_after_connect_failure()
        self.container_fut = asyncio.ensure_future(self._find_or_create_container())

        return await self.container_fut

    async def ensure_container_with_context(self, context: ComputerContext) -> str:
        return await self._ensure_container(context=context)

    async def ensure_page(self, context: ComputerContext):
        await self.ensure_container_with_context(context)
        await super().ensure_page(context)

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
        await _await_cleanup_without_waiting_for_cancellation(
            forwarder.close(),
            timeout_seconds=PLAYWRIGHT_FORWARDER_CLOSE_TIMEOUT_SECONDS,
        )

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

    @staticmethod
    def _websocket_check_target(*, ws_url: str) -> tuple[str, int, str]:
        parsed = urlsplit(ws_url)
        if parsed.hostname is None or parsed.port is None:
            raise ValueError(f"invalid playwright websocket url: {ws_url!r}")
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return parsed.hostname, parsed.port, path

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
                except Exception as error:
                    raise ConnectionError(
                        "playwright websocket endpoint is not ready yet"
                    ) from error
                if not isinstance(payload, dict):
                    raise ConnectionError(
                        "playwright websocket endpoint is not ready yet"
                    )
                ws_endpoint_path = payload.get("wsEndpointPath")
                if not isinstance(ws_endpoint_path, str) or ws_endpoint_path == "":
                    raise ConnectionError(
                        "playwright websocket endpoint is not ready yet"
                    )
                return self._ws_endpoint_url(
                    base_url=base_url,
                    ws_endpoint_path=ws_endpoint_path,
                )
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

        return await asyncio.wait_for(_probe(), timeout=timeout_seconds)

    async def _check_websocket_ready_once(
        self,
        *,
        ws_url: str,
        timeout_seconds: float,
    ) -> None:
        host, port, path = self._websocket_check_target(ws_url=ws_url)

        async def _probe() -> None:
            reader, writer = await asyncio.open_connection(host=host, port=port)
            try:
                websocket_key = base64.b64encode(os.urandom(16)).decode("ascii")
                request = (
                    f"GET {path} HTTP/1.1\r\n"
                    f"Host: {host}:{port}\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Key: {websocket_key}\r\n"
                    "Sec-WebSocket-Version: 13\r\n\r\n"
                )
                writer.write(request.encode("ascii"))
                await writer.drain()
                status_line = await reader.readline()
                if not status_line:
                    raise ConnectionError(
                        "playwright websocket endpoint is not ready yet"
                    )
                if not status_line.startswith(b"HTTP/"):
                    raise ConnectionError(
                        "playwright websocket endpoint is not ready yet"
                    )
                parts = status_line.split(maxsplit=2)
                if len(parts) < 2:
                    raise ConnectionError(
                        "playwright websocket endpoint is not ready yet"
                    )
                try:
                    status_code = int(parts[1])
                except ValueError as error:
                    raise ConnectionError(
                        "playwright websocket endpoint is not ready yet"
                    ) from error
                if status_code != 101:
                    raise ConnectionError(
                        "playwright websocket endpoint is not ready yet"
                    )
                while True:
                    line = await reader.readline()
                    if line in (b"", b"\r\n", b"\n"):
                        break
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

        await asyncio.wait_for(_probe(), timeout=timeout_seconds)

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
            page = await _wait_with_timeout_without_waiting_for_cancellation(
                browser.new_page(),
                timeout_seconds=timeout_seconds,
                timeout_error_message=(
                    "playwright browser session is still initializing"
                ),
            )
            await page.set_viewport_size({"width": width, "height": height})
            return browser, page
        except asyncio.CancelledError:
            await _await_cleanup_without_waiting_for_cancellation(
                browser.close(),
                timeout_seconds=timeout_seconds,
            )
            raise
        except Exception:
            await _await_cleanup_without_waiting_for_cancellation(
                browser.close(),
                timeout_seconds=timeout_seconds,
            )
            raise

    async def _get_browser_and_page(
        self, context: ComputerContext
    ) -> tuple[Browser, Page]:
        width, height = self.dimensions
        headers = {}
        loop = asyncio.get_running_loop()
        deadline: float | None = None
        if self.connect_timeout_seconds is not None:
            deadline = loop.time() + self.connect_timeout_seconds
        backoff_seconds = self.connect_backoff_initial_seconds
        attempt = 1

        try:
            while True:
                try:
                    if deadline is not None:
                        now = loop.time()
                        remaining_seconds = deadline - now
                        if remaining_seconds <= 0:
                            raise TimeoutError(
                                "timed out waiting for playwright websocket endpoint to become ready"
                            )

                    container_id = await self.ensure_container_with_context(context)
                    base_url = await self._base_url(
                        container_id=container_id,
                    )
                    remaining_seconds: float | None = None
                    if deadline is not None:
                        now = loop.time()
                        remaining_seconds = deadline - now
                    if remaining_seconds is not None and remaining_seconds <= 0:
                        raise TimeoutError(
                            "timed out waiting for playwright websocket endpoint to become ready"
                        )

                    attempt_timeout_seconds = self.connect_attempt_timeout_seconds
                    if remaining_seconds is not None:
                        attempt_timeout_seconds = min(
                            self.connect_attempt_timeout_seconds,
                            remaining_seconds,
                        )
                    logger.info(
                        "checking playwright endpoint (attempt %s): %s",
                        attempt,
                        self._health_check_url(base_url=base_url),
                    )
                    self._emit_startup_progress(
                        context=context,
                        details="Waiting for Playwright container to become ready.",
                    )
                    ws_endpoint_url = await self._check_server_ready_once(
                        base_url=base_url,
                        timeout_seconds=attempt_timeout_seconds,
                    )
                    if deadline is not None:
                        remaining_seconds = deadline - loop.time()
                    else:
                        remaining_seconds = None
                    if remaining_seconds is not None and remaining_seconds <= 0:
                        raise TimeoutError(
                            "timed out waiting for playwright websocket endpoint to become ready"
                        )
                    attempt_timeout_seconds = self.connect_attempt_timeout_seconds
                    if remaining_seconds is not None:
                        attempt_timeout_seconds = min(
                            self.connect_attempt_timeout_seconds,
                            remaining_seconds,
                        )
                    logger.info(
                        "checking playwright websocket endpoint (attempt %s): %s",
                        attempt,
                        ws_endpoint_url,
                    )
                    self._emit_startup_progress(
                        context=context,
                        details="Waiting for Playwright browser session to finish initializing.",
                    )
                    await self._check_websocket_ready_once(
                        ws_url=ws_endpoint_url,
                        timeout_seconds=attempt_timeout_seconds,
                    )
                    if deadline is not None:
                        remaining_seconds = deadline - loop.time()
                    else:
                        remaining_seconds = None
                    if remaining_seconds is not None and remaining_seconds <= 0:
                        raise TimeoutError(
                            "timed out waiting for playwright websocket endpoint to become ready"
                        )
                    attempt_timeout_seconds = self.connect_attempt_timeout_seconds
                    if remaining_seconds is not None:
                        attempt_timeout_seconds = min(
                            self.connect_attempt_timeout_seconds,
                            remaining_seconds,
                        )
                    logger.info(
                        "connecting to playwright websocket (attempt %s): %s",
                        attempt,
                        ws_endpoint_url,
                    )
                    self._emit_startup_progress(
                        context=context,
                        details="Connecting to Playwright browser session.",
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

                    remaining_seconds = None
                    if deadline is not None:
                        now = loop.time()
                        remaining_seconds = deadline - now
                    if remaining_seconds is not None and remaining_seconds <= 0:
                        raise TimeoutError(
                            "timed out waiting for playwright websocket endpoint to become ready"
                        ) from error

                    delay_seconds = backoff_seconds
                    if remaining_seconds is not None:
                        delay_seconds = min(backoff_seconds, remaining_seconds)
                    reason = str(error).strip()
                    if not reason:
                        reason = "playwright browser session is still initializing"
                    if reason in (
                        "playwright container is still starting",
                        "playwright websocket endpoint is not ready yet",
                    ):
                        self._emit_startup_progress(
                            context=context,
                            details="Waiting for Playwright container to become ready.",
                        )
                    else:
                        self._emit_startup_progress(
                            context=context,
                            details=(
                                "Waiting for Playwright browser session to finish "
                                f"initializing: {reason}"
                            ),
                        )
                    if reason == "playwright container is still starting":
                        logger.info(
                            (
                                "playwright container is still starting "
                                "(attempt %s); checking again in %.2fs"
                            ),
                            attempt,
                            delay_seconds,
                        )
                    elif reason == "playwright websocket endpoint is not ready yet":
                        logger.info(
                            (
                                "playwright endpoint is up but the websocket "
                                "endpoint is not ready yet (attempt %s); "
                                "checking again in %.2fs"
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
            context.emit_startup(
                state="cancelled",
                details=("Cancelled while starting Playwright browser session.",),
            )
            await self._close_forwarder()
            raise
        except Exception:
            context.emit_startup(
                state="failed",
                details=("Failed to start Playwright browser session.",),
            )
            await self._close_forwarder()
            raise

        logger.info("playwright ready")
        context.emit_startup(
            state="completed",
            details=("Playwright browser session ready.",),
        )
        await page.goto(self.starting_url)
        return browser, page
