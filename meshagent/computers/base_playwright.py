import asyncio
import contextlib
import time
import base64
import os
from collections.abc import Awaitable
from typing import List, Dict, Literal
from playwright.async_api import async_playwright, Browser, Page, Route, Request
from meshagent.computers.utils import check_blocklisted_url
from .computer import ComputerContext

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

_DEFAULT_PLAYWRIGHT_DIMENSIONS = (1440, 900)
_SUPPORTED_PLAYWRIGHT_DIMENSIONS = {
    (1440, 900),
    (1600, 900),
}
_PLAYWRIGHT_DIMENSIONS_ENV_VAR = "MESHAGENT_PLAYWRIGHT_DIMENSIONS"
DEFAULT_PLAYWRIGHT_STARTING_URL = "https://google.com"
_PLAYWRIGHT_CONTEXT_RESTART_TIMEOUT_SECONDS = 5.0


def _discard_task_result(task: asyncio.Task[object]) -> None:
    def _consume_result(done_task: asyncio.Task[object]) -> None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            done_task.result()

    if task.done():
        _consume_result(task)
        return
    task.add_done_callback(_consume_result)


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


def _parse_dimensions(raw_value: str) -> tuple[int, int] | None:
    normalized = raw_value.strip().lower()
    if normalized == "":
        return None

    if "x" in normalized:
        parts = normalized.split("x", maxsplit=1)
    elif "," in normalized:
        parts = normalized.split(",", maxsplit=1)
    else:
        return None

    try:
        width = int(parts[0].strip())
        height = int(parts[1].strip())
    except ValueError:
        return None

    return (width, height)


def _resolve_playwright_dimensions() -> tuple[int, int]:
    raw_dimensions = os.getenv(_PLAYWRIGHT_DIMENSIONS_ENV_VAR)
    if raw_dimensions is None:
        return _DEFAULT_PLAYWRIGHT_DIMENSIONS

    parsed = _parse_dimensions(raw_dimensions)
    if parsed is None or parsed not in _SUPPORTED_PLAYWRIGHT_DIMENSIONS:
        return _DEFAULT_PLAYWRIGHT_DIMENSIONS

    return parsed


def _is_supported_playwright_dimensions(dimensions: tuple[int, int]) -> bool:
    return dimensions in _SUPPORTED_PLAYWRIGHT_DIMENSIONS


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
    dimensions = _DEFAULT_PLAYWRIGHT_DIMENSIONS

    def __init__(
        self,
        dimensions: tuple[int, int] | None = None,
        starting_url: str | None = None,
    ):
        self._context = None
        self._playwright = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self.starting_url = (
            starting_url
            if isinstance(starting_url, str) and starting_url.strip() != ""
            else DEFAULT_PLAYWRIGHT_STARTING_URL
        )
        if dimensions is None:
            self.dimensions = _resolve_playwright_dimensions()
        elif _is_supported_playwright_dimensions(dimensions):
            self.dimensions = dimensions
        else:
            raise ValueError(
                "playwright dimensions must be one of: (1440, 900), (1600, 900)"
            )

    async def __aenter__(self, context: ComputerContext):
        # Start Playwright and call the subclass hook for getting browser/page
        self._context = async_playwright()
        self._playwright = await self._context.__aenter__()
        try:
            self._browser, self._page = await self._get_browser_and_page(context)
        except Exception:
            await _await_cleanup_without_waiting_for_cancellation(
                self._context.__aexit__(None, None, None),
                timeout_seconds=_PLAYWRIGHT_CONTEXT_RESTART_TIMEOUT_SECONDS,
            )
            self._context = None
            self._playwright = None
            self._browser = None
            self._page = None
            raise

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
            await _await_cleanup_without_waiting_for_cancellation(
                self._browser.close(),
                timeout_seconds=_PLAYWRIGHT_CONTEXT_RESTART_TIMEOUT_SECONDS,
            )
        if self._playwright and self._context is not None:
            await _await_cleanup_without_waiting_for_cancellation(
                self._context.__aexit__(exc_type, exc_val, exc_tb),
                timeout_seconds=_PLAYWRIGHT_CONTEXT_RESTART_TIMEOUT_SECONDS,
            )
        self._context = None
        self._playwright = None
        self._browser = None
        self._page = None

    async def restart_playwright_client(self) -> None:
        old_browser = self._browser
        old_context = self._context

        self._browser = None
        self._page = None
        self._playwright = None
        self._context = None

        if old_browser is not None:
            await _await_cleanup_without_waiting_for_cancellation(
                old_browser.close(),
                timeout_seconds=_PLAYWRIGHT_CONTEXT_RESTART_TIMEOUT_SECONDS,
            )

        if old_context is not None:
            await _await_cleanup_without_waiting_for_cancellation(
                old_context.__aexit__(None, None, None),
                timeout_seconds=_PLAYWRIGHT_CONTEXT_RESTART_TIMEOUT_SECONDS,
            )

        self._context = async_playwright()
        self._playwright = await self._context.__aenter__()

    async def ensure_page(self, context: ComputerContext):
        # After a timeout, we might loose our browser
        if (
            self._page is None
            or self._browser is None
            or not self._browser.is_connected()
        ):
            self._browser, self._page = await self._get_browser_and_page(context)

    # --- Common "Computer" actions ---

    async def screenshot_bytes(
        self,
        context: ComputerContext,
        *,
        full_page: bool = False,
    ) -> bytes:
        await self.ensure_page(context)
        png_bytes = await self._page.screenshot(full_page=full_page)
        return png_bytes

    async def screenshot(
        self,
        context: ComputerContext,
        *,
        full_page: bool = False,
    ) -> str:
        await self.ensure_page(context)
        png_bytes = await self.screenshot_bytes(context, full_page=full_page)
        return base64.b64encode(png_bytes).decode("utf-8")

    async def click(
        self,
        context: ComputerContext,
        *,
        x: int,
        y: int,
        button: str = "left",
    ) -> None:
        await self.ensure_page(context)
        match button:
            case "back":
                await self.back(context)
            case "forward":
                await self.forward(context)
            case "wheel":
                await self._page.mouse.wheel(x, y)
            case _:
                button_mapping = {"left": "left", "right": "right"}
                button_type = button_mapping.get(button, "left")
                await self._page.mouse.click(x, y, button=button_type)

    async def double_click(self, context: ComputerContext, *, x: int, y: int) -> None:
        await self.ensure_page(context)
        await self._page.mouse.dblclick(x, y)

    async def scroll(
        self,
        context: ComputerContext,
        *,
        x: int,
        y: int,
        scroll_x: int,
        scroll_y: int,
    ) -> None:
        await self.ensure_page(context)
        await self._page.mouse.move(x, y)
        await self._page.evaluate(f"window.scrollBy({scroll_x}, {scroll_y})")

    async def type(self, context: ComputerContext, *, text: str) -> None:
        await self.ensure_page(context)
        await self._page.keyboard.type(text)

    async def wait(self, context: ComputerContext, *, ms: int = 1000) -> None:
        await self.ensure_page(context)
        time.sleep(ms / 1000)

    async def move(self, context: ComputerContext, *, x: int, y: int) -> None:
        await self.ensure_page(context)
        await self._page.mouse.move(x, y)

    async def keypress(self, context: ComputerContext, *, keys: List[str]) -> None:
        await self.ensure_page(context)
        for key in keys:
            mapped_key = CUA_KEY_TO_PLAYWRIGHT_KEY.get(key.lower(), key)
            await self._page.keyboard.press(mapped_key)

    async def drag(
        self,
        context: ComputerContext,
        *,
        path: List[Dict[str, int]],
    ) -> None:
        await self.ensure_page(context)
        if not path:
            return

        await self._page.mouse.move(path[0]["x"], path[0]["y"])
        await self._page.mouse.down()
        for point in path[1:]:
            await self._page.mouse.move(point["x"], point["y"])
        await self._page.mouse.up()

    async def get_current_url(self, context: ComputerContext) -> str:
        await self.ensure_page(context)
        return self._page.url

    # --- Extra browser-oriented actions ---
    async def goto(self, context: ComputerContext, *, url: str) -> None:
        await self.ensure_page(context)
        try:
            return await self._page.goto(url)
        except Exception as e:
            print(f"Error navigating to {url}: {e}")

    async def back(self, context: ComputerContext) -> None:
        await self.ensure_page(context)
        return await self._page.go_back()

    async def forward(self, context: ComputerContext) -> None:
        await self.ensure_page(context)
        return await self._page.go_forward()

    # --- Subclass hook ---
    async def _get_browser_and_page(
        self, context: ComputerContext
    ) -> tuple[Browser, Page]:
        """Subclasses must implement, returning (Browser, Page)."""
        del context
        raise NotImplementedError
