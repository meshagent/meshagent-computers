import asyncio
import logging
import os
import re
from importlib.metadata import PackageNotFoundError, version as package_version

from playwright.async_api import Browser, Page

from meshagent.api import RoomClient
from meshagent.api.port_forward import LocalExposeHandle, port_forward

from .base_playwright import BasePlaywrightComputer


logger = logging.getLogger("computer_use")
PLAYWRIGHT_CONTAINER_NAME = "playwright"
PLAYWRIGHT_REMOTE_PORT = 3000
PLAYWRIGHT_CONNECT_TIMEOUT_SECONDS = 30.0
PLAYWRIGHT_CONNECT_BACKOFF_INITIAL_SECONDS = 0.25
PLAYWRIGHT_CONNECT_BACKOFF_MAX_SECONDS = 4.0


def _playwright_version() -> str:
    try:
        raw_version = package_version("playwright")
    except PackageNotFoundError as exc:
        raise RuntimeError("playwright is not installed") from exc

    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", raw_version)
    if match is None:
        raise RuntimeError(f"unsupported playwright version format: {raw_version!r}")

    major, minor, patch = match.groups()
    return f"{major}.{minor}.{patch}"


class ContainerPlaywrightComputer(BasePlaywrightComputer):
    """Launches a containerized Chromium instance using Playwright."""

    def __init__(
        self,
        *,
        headless: bool = False,
        image: str | None = None,
        room: RoomClient,
    ):
        super().__init__()
        self.headless = headless
        self.playwright_version = _playwright_version()
        self.image = image or (
            f"mcr.microsoft.com/playwright:v{self.playwright_version}-noble"
        )
        self.container_name = PLAYWRIGHT_CONTAINER_NAME
        self.container_command = (
            '/bin/sh -c "npx -y playwright'
            f"@{self.playwright_version}"
            f' run-server --port {PLAYWRIGHT_REMOTE_PORT} --host 0.0.0.0"'
        )
        self.room = room
        self.container_fut = None
        self._forwarder: LocalExposeHandle | None = None
        self.connect_timeout_seconds = PLAYWRIGHT_CONNECT_TIMEOUT_SECONDS
        self.connect_backoff_initial_seconds = (
            PLAYWRIGHT_CONNECT_BACKOFF_INITIAL_SECONDS
        )
        self.connect_backoff_max_seconds = PLAYWRIGHT_CONNECT_BACKOFF_MAX_SECONDS

    async def _find_or_create_container(self):
        containers = await self.room.containers.list()

        for container in containers:
            if container.name != self.container_name:
                continue

            if container.image != self.image:
                logger.info(
                    "playwright container image mismatch, recreating: %s != %s",
                    container.image,
                    self.image,
                )
                await self.room.containers.delete(container_id=container.id)
                break

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

    def _use_port_forward(self) -> bool:
        return (
            os.getenv("MESHAGENT_SESSION_ID") is None
            or os.getenv("MESHAGENT_TUNNEL_PLAYWRIGHT") is not None
        )

    async def _close_forwarder(self) -> None:
        if self._forwarder is None:
            return
        forwarder = self._forwarder
        self._forwarder = None
        await forwarder.close()

    async def _base_url(self, *, container_id: str, use_port_forward: bool) -> str:
        if not use_port_forward:
            return f"ws://127.0.0.1:{PLAYWRIGHT_REMOTE_PORT}/"

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

    async def _get_browser_and_page(self) -> tuple[Browser, Page]:
        container_id = await self.ensure_container()

        width, height = self.dimensions
        headers = {}
        use_port_forward = self._use_port_forward()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.connect_timeout_seconds
        backoff_seconds = self.connect_backoff_initial_seconds
        attempt = 1

        while True:
            try:
                base_url = await self._base_url(
                    container_id=container_id,
                    use_port_forward=use_port_forward,
                )
                logger.info(
                    "connecting to playwright (attempt %s): %s", attempt, base_url
                )
                browser = await self._playwright.chromium.connect(
                    base_url, headers=headers
                )
                break
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if self._is_version_mismatch_error(error):
                    raise

                if not use_port_forward:
                    raise

                now = loop.time()
                remaining_seconds = deadline - now
                if remaining_seconds <= 0:
                    await self._close_forwarder()
                    raise TimeoutError(
                        "timed out waiting for playwright port forward to become ready"
                    ) from error

                delay_seconds = min(backoff_seconds, remaining_seconds)
                logger.warning(
                    (
                        "failed to connect to playwright over port forward on attempt %s; "
                        "retrying in %.2fs"
                    ),
                    attempt,
                    delay_seconds,
                    exc_info=error,
                )
                await self._close_forwarder()
                await asyncio.sleep(delay_seconds)
                backoff_seconds = min(
                    backoff_seconds * 2,
                    self.connect_backoff_max_seconds,
                )
                attempt += 1

        logger.info("starting a new browser page")
        page = await browser.new_page()
        await page.set_viewport_size({"width": width, "height": height})
        await page.goto("https://google.com")
        return browser, page
