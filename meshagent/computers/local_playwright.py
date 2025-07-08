from playwright.async_api import Browser, Page
from .base_playwright import BasePlaywrightComputer


class LocalPlaywrightComputer(BasePlaywrightComputer):
    """Launches a local Chromium instance using Playwright."""

    def __init__(self, headless: bool = False):
        super().__init__()
        self.headless = headless

    async def _get_browser_and_page(self) -> tuple[Browser, Page]:
        width, height = self.dimensions
        launch_args = [
            f"--window-size={width},{height}",
            "--disable-extensions",
            "--disable-file-system",
        ]
        browser = await self._playwright.chromium.launch(
            chromium_sandbox=True, headless=self.headless, args=launch_args, env={}
        )
        page = await browser.new_page()
        await page.set_viewport_size({"width": width, "height": height})
        await page.goto("https://google.com")
        return browser, page
