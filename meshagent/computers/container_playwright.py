import asyncio
import errno
import logging
import os
import re
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version as package_version

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


def _playwright_version() -> str:
    try:
        raw_version = package_version("playwright")
    except PackageNotFoundError:
        try:
            repo_version_module = import_module("playwright._repo_version")
            raw_version = repo_version_module.version
        except Exception as exc:
            raise RuntimeError("playwright is not installed") from exc
        logger.warning(
            "playwright package metadata is unavailable; "
            "using playwright._repo_version.version fallback"
        )

    if not isinstance(raw_version, str):
        raise RuntimeError("playwright is not installed")

    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", raw_version)
    if match is None:
        raise RuntimeError(f"unsupported playwright version format: {raw_version!r}")

    major, minor, patch = match.groups()
    return f"{major}.{minor}.{patch}"


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
    ):
        super().__init__(dimensions=dimensions)
        self.headless = headless
        self.playwright_version = _playwright_version()
        self.image = image or "meshagent/playwright:default"
        self.container_name = PLAYWRIGHT_CONTAINER_NAME
        self.container_command = (
            '/bin/sh -c "npx -y playwright'
            f"@{self.playwright_version}"
            f' run-server --port {PLAYWRIGHT_REMOTE_PORT} --host 0.0.0.0"'
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

        if isinstance(error, PlaywrightError):
            message = str(error).lower()
            return (
                "econnrefused" in message
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
            "econnrefused" in message
            or "connection refused" in message
            or "socket hang up" in message
            or "econnreset" in message
            or "connection reset" in message
        )

    async def _connect_browser_and_page_once(
        self,
        *,
        base_url: str,
        headers: dict[str, str],
        width: int,
        height: int,
    ) -> tuple[Browser, Page]:
        browser = await self._playwright.chromium.connect(base_url, headers=headers)
        try:
            page = await browser.new_page()
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

        while True:
            try:
                base_url = await self._base_url(
                    container_id=container_id,
                )
                logger.info(
                    "connecting to playwright (attempt %s): %s", attempt, base_url
                )
                now = loop.time()
                remaining_seconds = deadline - now
                if remaining_seconds <= 0:
                    await self._close_forwarder()
                    raise TimeoutError(
                        "timed out waiting for playwright websocket endpoint to become ready"
                    )

                attempt_timeout_seconds = min(
                    self.connect_attempt_timeout_seconds,
                    remaining_seconds,
                )
                browser, page = await asyncio.wait_for(
                    self._connect_browser_and_page_once(
                        base_url=base_url,
                        headers=headers,
                        width=width,
                        height=height,
                    ),
                    timeout=attempt_timeout_seconds,
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
                    await self._close_forwarder()
                    raise TimeoutError(
                        "timed out waiting for playwright websocket endpoint to become ready"
                    ) from error

                delay_seconds = min(backoff_seconds, remaining_seconds)
                logger.info(
                    (
                        "failed to connect to playwright on attempt %s; "
                        "retrying in %.2fs (%s)"
                    ),
                    attempt,
                    delay_seconds,
                    error,
                )
                logger.debug(
                    "playwright connect retryable error details",
                    exc_info=error,
                )
                await self._close_forwarder()
                await asyncio.sleep(delay_seconds)
                backoff_seconds = min(
                    backoff_seconds * 2,
                    self.connect_backoff_max_seconds,
                )
                attempt += 1

        logger.info("playwright ready")
        await page.goto("https://google.com")
        return browser, page
