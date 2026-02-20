import asyncio
import logging
import os
import re
from importlib.metadata import PackageNotFoundError, version as package_version

from playwright.async_api import Browser, Page

from meshagent.api import RoomClient
from meshagent.api.port_forward import port_forward

from .base_playwright import BasePlaywrightComputer


logger = logging.getLogger("computer_use")
PLAYWRIGHT_CONTAINER_NAME = "playwright"


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
            ' run-server --port 3000 --host 0.0.0.0"'
        )
        self.room = room
        self.container_fut = None
        self._forwarder = None

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

    async def _get_browser_and_page(self) -> tuple[Browser, Page]:
        container_id = await self.ensure_container()

        width, height = self.dimensions
        headers = {}
        if os.getenv("MESHAGENT_SESSION_ID") is None or os.getenv(
            "MESHAGENT_TUNNEL_PLAYWRIGHT"
        ):
            logger.info("exposing local port forward for remote playwright container")
            self._forwarder = await port_forward(
                container_id=container_id,
                port=3000,
                token=self.room.protocol.token,
            )

            base_url = f"ws://{self._forwarder.host}:{self._forwarder.port}/"

        else:
            base_url = "ws://127.0.0.1:3000/"

        logger.info("connecting to playwright")
        browser = await self._playwright.chromium.connect(base_url, headers=headers)
        logger.info("starting a new browser page")
        page = await browser.new_page()
        await page.set_viewport_size({"width": width, "height": height})
        await page.goto("https://google.com")
        return browser, page
