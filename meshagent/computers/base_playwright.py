import time
import base64
from typing import List, Dict, Literal
from playwright.async_api import async_playwright, Browser, Page, Route, Request
from meshagent.computers.utils import check_blocklisted_url

# Optional: key mapping if your model uses "CUA" style keys
CUA_KEY_TO_PLAYWRIGHT_KEY = {
    "/": "Divide",
    "\\": "Backslash",
    "alt": "Alt",
    "arrowdown": "ArrowDown",
    "arrowleft": "ArrowLeft",
    "arrowright": "ArrowRight",
    "arrowup": "ArrowUp",
    "backspace": "Backspace",
    "capslock": "CapsLock",
    "cmd": "Meta",
    "ctrl": "Control",
    "delete": "Delete",
    "end": "End",
    "enter": "Enter",
    "esc": "Escape",
    "home": "Home",
    "insert": "Insert",
    "option": "Alt",
    "pagedown": "PageDown",
    "pageup": "PageUp",
    "shift": "Shift",
    "space": " ",
    "super": "Meta",
    "tab": "Tab",
    "win": "Meta",
}


class BasePlaywrightComputer:
    """
    Abstract base for Playwright-based computers:

      - Subclasses override `_get_browser_and_page()` to do local or remote connection,
        returning (Browser, Page).
      - This base class handles context creation (`__enter__`/`__exit__`),
        plus standard "Computer" actions like click, scroll, etc.
      - We also have extra browser actions: `goto(url)` and `back()`.
    """

    environment: Literal["browser"] = "browser"
    dimensions = (1024, 768)

    def __init__(self):
        self._playwright = None
        self._browser: Browser | None = None
        self._page: Page | None = None

    async def __aenter__(self):
        # Start Playwright and call the subclass hook for getting browser/page
        self._context = async_playwright()
        self._playwright = await self._context.__aenter__()
        self._browser, self._page = await self._get_browser_and_page()

        # Set up network interception to flag URLs matching domains in BLOCKED_DOMAINS
        async def handle_route(route: Route, request: Request):
            url = request.url
            if check_blocklisted_url(url):
                print(f"Flagging blocked domain: {url}")
                await route.abort()
            else:
                await route.continue_()

        await self._page.route("**/*", handle_route)

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._context.__aexit__(exc_type, exc_val, exc_tb)

    async def ensure_page(self):
        # After a timeout, we might loose our browser
        if self._page is None or not self._browser.is_connected:
            self._browser, self._page = await self._get_browser_and_page()

    # --- Common "Computer" actions ---

    async def screenshot_bytes(self, full_page: bool = False) -> bytes:
        await self.ensure_page()
        png_bytes = await self._page.screenshot(full_page=full_page)
        return png_bytes

    async def screenshot(self, full_page: bool = False) -> str:
        await self.ensure_page()
        png_bytes = await self.screenshot_bytes(full_page=full_page)
        return base64.b64encode(png_bytes).decode("utf-8")

    async def click(self, x: int, y: int, button: str = "left") -> None:
        await self.ensure_page()
        match button:
            case "back":
                await self.back()
            case "forward":
                await self.forward()
            case "wheel":
                await self._page.mouse.wheel(x, y)
            case _:
                button_mapping = {"left": "left", "right": "right"}
                button_type = button_mapping.get(button, "left")
                await self._page.mouse.click(x, y, button=button_type)

    async def double_click(self, x: int, y: int) -> None:
        await self.ensure_page()
        await self._page.mouse.dblclick(x, y)

    async def scroll(self, x: int, y: int, scroll_x: int, scroll_y: int) -> None:
        await self.ensure_page()
        await self._page.mouse.move(x, y)
        await self._page.evaluate(f"window.scrollBy({scroll_x}, {scroll_y})")

    async def type(self, text: str) -> None:
        await self.ensure_page()
        await self._page.keyboard.type(text)

    async def wait(self, ms: int = 1000) -> None:
        await self.ensure_page()
        time.sleep(ms / 1000)

    async def move(self, x: int, y: int) -> None:
        await self.ensure_page()
        await self._page.mouse.move(x, y)

    async def keypress(self, keys: List[str]) -> None:
        await self.ensure_page()
        for key in keys:
            mapped_key = CUA_KEY_TO_PLAYWRIGHT_KEY.get(key.lower(), key)
            await self._page.keyboard.press(mapped_key)

    async def drag(self, path: List[Dict[str, int]]) -> None:
        await self.ensure_page()
        if not path:
            return

        await self._page.mouse.move(path[0]["x"], path[0]["y"])
        await self._page.mouse.down()
        for point in path[1:]:
            await self._page.mouse.move(point["x"], point["y"])
        await self._page.mouse.up()

    async def get_current_url(self) -> str:
        await self.ensure_page()
        return self._page.url

    # --- Extra browser-oriented actions ---
    async def goto(self, url: str) -> None:
        await self.ensure_page()
        try:
            return await self._page.goto(url)
        except Exception as e:
            print(f"Error navigating to {url}: {e}")

    async def back(self) -> None:
        await self.ensure_page()
        return await self._page.go_back()

    async def forward(self) -> None:
        await self.ensure_page()
        return await self._page.go_forward()

    # --- Subclass hook ---
    async def _get_browser_and_page(self) -> tuple[Browser, Page]:
        """Subclasses must implement, returning (Browser, Page)."""
        raise NotImplementedError
