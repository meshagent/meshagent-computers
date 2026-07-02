from types import SimpleNamespace

import pytest

from meshagent.computers import browserbase as browserbase_module
from meshagent.computers.browserbase import BrowserbaseBrowser
from meshagent.computers.computer import ComputerContext


class _FakeParticipant:
    def get_attribute(self, key: str):
        del key
        return None


class _FakeRoom:
    local_participant = _FakeParticipant()


class _FakeBrowserbaseSessions:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, object]] = []

    async def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return SimpleNamespace(id="session-1", connect_url="wss://connect.test")


class _FakeBrowserbase:
    instances: list["_FakeBrowserbase"] = []

    def __init__(self, *, api_key: str | None = None):
        self.api_key = api_key
        self.sessions = _FakeBrowserbaseSessions()
        self.instances.append(self)


class _FakePage:
    def __init__(self) -> None:
        self.goto_calls: list[str] = []
        self.handlers: list[tuple[str, object]] = []

    def on(self, event: str, handler) -> None:
        self.handlers.append((event, handler))

    async def goto(self, url: str) -> None:
        self.goto_calls.append(url)


class _FakeBrowserContext:
    def __init__(self, page: _FakePage) -> None:
        self.pages = [page]
        self.handlers: list[tuple[str, object]] = []
        self.init_scripts: list[str] = []

    def on(self, event: str, handler) -> None:
        self.handlers.append((event, handler))

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)


class _FakeBrowser:
    def __init__(self, context: _FakeBrowserContext) -> None:
        self.contexts = [context]


class _FakeChromium:
    def __init__(self, browser: _FakeBrowser) -> None:
        self.browser = browser
        self.connect_calls: list[dict[str, object]] = []

    async def connect_over_cdp(self, connect_url: str, *, timeout: int):
        self.connect_calls.append({"connect_url": connect_url, "timeout": timeout})
        return self.browser


def _make_context() -> ComputerContext:
    room = _FakeRoom()
    return ComputerContext(room=room, caller=room.local_participant)


@pytest.mark.asyncio
async def test_browserbase_get_browser_and_page_matches_python_session_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeBrowserbase.instances.clear()
    monkeypatch.setenv("BROWSERBASE_API_KEY", "api-key")
    monkeypatch.setenv("BROWSERBASE_PROJECT_ID", "project-id")
    monkeypatch.setattr(browserbase_module, "AsyncBrowserbase", _FakeBrowserbase)

    page = _FakePage()
    browser_context = _FakeBrowserContext(page)
    browser = _FakeBrowser(browser_context)
    chromium = _FakeChromium(browser)

    computer = BrowserbaseBrowser(
        width=1200,
        height=700,
        region="eu-central-1",
        proxy=True,
        virtual_mouse=True,
        ad_blocker=True,
        starting_url="  https://browserbase.test  ",
    )
    computer._playwright = SimpleNamespace(chromium=chromium)

    returned_browser, returned_page = await computer._get_browser_and_page(
        _make_context()
    )

    assert returned_browser is browser
    assert returned_page is page
    assert computer.session.id == "session-1"
    assert _FakeBrowserbase.instances[0].api_key == "api-key"
    assert _FakeBrowserbase.instances[0].sessions.create_calls == [
        {
            "project_id": "project-id",
            "browser_settings": {
                "viewport": {"width": 1200, "height": 700},
                "blockAds": True,
            },
            "region": "eu-central-1",
            "proxies": True,
        }
    ]
    assert chromium.connect_calls == [
        {"connect_url": "wss://connect.test", "timeout": 60000}
    ]
    assert browser_context.handlers == [("page", computer._handle_new_page)]
    assert len(browser_context.init_scripts) == 1
    assert page.handlers == [("close", computer._handle_page_close)]
    assert page.goto_calls == ["  https://browserbase.test  "]
