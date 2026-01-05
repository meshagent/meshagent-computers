from playwright.async_api import Browser, Page
from .base_playwright import BasePlaywrightComputer
from meshagent.api import RoomClient
import asyncio
import os
import logging

from meshagent.api.port_forward import port_forward


logger = logging.getLogger("computer_use")


class ContainerPlaywrightComputer(BasePlaywrightComputer):
    """Launches a containerized Chromium instance using Playwright."""

    def __init__(
        self,
        *,
        headless: bool = False,
        image: str = "mcr.microsoft.com/playwright:v1.57.0-noble",
        room: RoomClient,
    ):
        super().__init__()
        self.headless = headless
        self.image = image
        self.room = room
        self.container_fut = None
        self._forwarder = None

    async def _find_or_create_container(self):
        containers = await self.room.containers.list()

        for container in containers:
            if container.name == "playwright":
                logger.info("playwright container found, using existing container")
                return container.id

        logger.info("playwright container not found, spinning up")
        return await self.room.containers.run(
            name="playwright",
            image=self.image,
            command='/bin/sh -c "npx -y playwright@1.57.0 run-server --port 3000 --host 0.0.0.0"',
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
