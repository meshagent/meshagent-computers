import os
import time
from dotenv import load_dotenv
from scrapybara import AsyncScrapybara
from playwright.async_api import async_playwright, Browser, Page
from meshagent.computers.utils import BLOCKED_DOMAINS

load_dotenv()

CUA_KEY_TO_SCRAPYBARA_KEY = {
    "/": "slash",
    "\\": "backslash",
    "arrowdown": "Down",
    "arrowleft": "Left",
    "arrowright": "Right",
    "arrowup": "Up",
    "backspace": "BackSpace",
    "capslock": "Caps_Lock",
    "cmd": "Meta_L",
    "delete": "Delete",
    "end": "End",
    "enter": "Return",
    "esc": "Escape",
    "home": "Home",
    "insert": "Insert",
    "option": "Alt_L",
    "pagedown": "Page_Down",
    "pageup": "Page_Up",
    "tab": "Tab",
    "win": "Meta_L",
}


class ScrapybaraBrowser:
    """
    Scrapybara provides virtual desktops and browsers in the cloud. https://scrapybara.com
    You can try OpenAI CUA for free at https://computer.new or read our CUA Quickstart at https://computer.new/cua.
    """

    def __init__(self):
        self.client = AsyncScrapybara(api_key=os.getenv("SCRAPYBARA_API_KEY"))
        self.environment = "browser"
        self.dimensions = (1024, 768)
        self._playwright = None
        self._browser: Browser | None = None
        self._page: Page | None = None

    async def __aenter__(self):
        print("Starting scrapybara browser")
        blocked_domains = [
            domain.replace("https://", "").replace("www.", "")
            for domain in BLOCKED_DOMAINS
        ]
        self.instance = await self.client.start_browser(blocked_domains=blocked_domains)
        print("Scrapybara browser started ₍ᐢ•(ܫ)•ᐢ₎")
        print(
            f"You can view and interact with the stream at {self.instance.get_stream_url().stream_url}"
        )
        self._playwright_context = async_playwright()
        self._playwright = await self._playwright_context.__aenter__()
        self._browser = await self._playwright.chromium.connect_over_cdp(
            (await self.instance.get_cdp_url()).cdp_url
        )
        self._page = self._browser.contexts[0].pages[0]
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._playwright_context.__aexit__(exc_type, exc_val, exc_tb)

        print("Stopping scrapybara browser")
        await self.instance.stop()
        print("Scrapybara browser stopped ₍ᐢ-(ｪ)-ᐢ₎")

    async def goto(self, url: str) -> None:
        await self._page.goto(url)

    async def get_current_url(self) -> str:
        return (await self.instance.get_current_url()).current_url

    async def screenshot(self) -> str:
        return (await self.instance.screenshot()).base_64_image

    async def click(self, x: int, y: int, button: str = "left") -> None:
        button = "middle" if button == "wheel" else button
        await self.instance.computer(
            action="click_mouse",
            click_type="click",
            button=button,
            coordinates=[x, y],
            num_clicks=1,
        )

    async def double_click(self, x: int, y: int) -> None:
        await self.instance.computer(
            action="click_mouse",
            click_type="click",
            button="left",
            coordinates=[x, y],
            num_clicks=2,
        )

    async def scroll(self, x: int, y: int, scroll_x: int, scroll_y: int) -> None:
        await self.instance.computer(
            action="scroll",
            coordinates=[x, y],
            delta_x=scroll_x // 20,
            delta_y=scroll_y // 20,
        )

    async def type(self, text: str) -> None:
        await self.instance.computer(action="type_text", text=text)

    async def wait(self, ms: int = 1000) -> None:
        time.sleep(ms / 1000)
        # Scrapybara also has `self.instance.computer(action="wait", duration=ms / 1000)`

    async def move(self, x: int, y: int) -> None:
        await self.instance.computer(action="move_mouse", coordinates=[x, y])

    async def keypress(self, keys: list[str]) -> None:
        mapped_keys = [
            CUA_KEY_TO_SCRAPYBARA_KEY.get(key.lower(), key.lower()) for key in keys
        ]
        await self.instance.computer(action="press_key", keys=mapped_keys)

    async def drag(self, path: list[dict[str, int]]) -> None:
        if not path:
            return
        path = [[point["x"], point["y"]] for point in path]
        await self.instance.computer(action="drag_mouse", path=path)


class ScrapybaraUbuntu:
    """
    Scrapybara provides virtual desktops and browsers in the cloud.
    You can try OpenAI CUA for free at https://computer.new or read our CUA Quickstart at https://computer.new/cua.
    """

    def __init__(self):
        self.client = AsyncScrapybara(api_key=os.getenv("SCRAPYBARA_API_KEY"))
        self.environment = "linux"  # "windows", "mac", "linux", or "browser"
        self.dimensions = (1024, 768)

    async def __aenter__(self):
        print("Starting Scrapybara Ubuntu instance")
        blocked_domains = [
            domain.replace("https://", "").replace("www.", "")
            for domain in BLOCKED_DOMAINS
        ]
        self.instance = await self.client.start_ubuntu(blocked_domains=blocked_domains)
        print("Scrapybara Ubuntu instance started ₍ᐢ•(ܫ)•ᐢ₎")
        print(
            f"You can view and interact with the stream at {self.instance.get_stream_url().stream_url}"
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        print("Stopping Scrapybara Ubuntu instance")
        (await self.instance).stop()
        print("Scrapybara Ubuntu instance stopped ₍ᐢ-(ｪ)-ᐢ₎")

    async def screenshot(self) -> str:
        return (await self.instance.screenshot()).base_64_image

    async def click(self, x: int, y: int, button: str = "left") -> None:
        button = "middle" if button == "wheel" else button
        await self.instance.computer(
            action="click_mouse",
            click_type="click",
            button=button,
            coordinates=[x, y],
            num_clicks=1,
        )

    async def double_click(self, x: int, y: int) -> None:
        await self.instance.computer(
            action="click_mouse",
            click_type="click",
            button="left",
            coordinates=[x, y],
            num_clicks=2,
        )

    async def scroll(self, x: int, y: int, scroll_x: int, scroll_y: int) -> None:
        await self.instance.computer(
            action="scroll",
            coordinates=[x, y],
            delta_x=scroll_x // 20,
            delta_y=scroll_y // 20,
        )

    async def type(self, text: str) -> None:
        await self.instance.computer(action="type_text", text=text)

    async def wait(self, ms: int = 1000) -> None:
        time.sleep(ms / 1000)
        # Scrapybara also has `self.instance.computer(action="wait", duration=ms / 1000)`

    async def move(self, x: int, y: int) -> None:
        await self.instance.computer(action="move_mouse", coordinates=[x, y])

    async def keypress(self, keys: list[str]) -> None:
        mapped_keys = [
            CUA_KEY_TO_SCRAPYBARA_KEY.get(key.lower(), key.lower()) for key in keys
        ]
        await self.instance.computer(action="press_key", keys=mapped_keys)

    async def drag(self, path: list[dict[str, int]]) -> None:
        if not path:
            return
        path = [[point["x"], point["y"]] for point in path]
        await self.instance.computer(action="drag_mouse", path=path)
