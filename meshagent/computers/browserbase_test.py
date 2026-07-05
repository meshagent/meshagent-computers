from types import SimpleNamespace

import pytest
from playwright.async_api import Error as PlaywrightError

from meshagent.computers import browserbase as browserbase_module
from meshagent.computers.base_playwright import BasePlaywrightComputer
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
        self.context = None
        self.close_calls = 0
        self.close_error: Exception | None = None

    def on(self, event: str, handler) -> None:
        self.handlers.append((event, handler))

    async def goto(self, url: str) -> None:
        self.goto_calls.append(url)

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class _FakeBrowserContext:
    def __init__(self, page: _FakePage) -> None:
        self.pages = [page]
        page.context = self
        self.handlers: list[tuple[str, object]] = []
        self.init_scripts: list[str] = []
        self.cdp_session = None

    def on(self, event: str, handler) -> None:
        self.handlers.append((event, handler))

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    async def new_cdp_session(self, page: _FakePage):
        assert page is self.pages[0]
        return self.cdp_session


class _FakeBrowser:
    def __init__(self, context: _FakeBrowserContext) -> None:
        self.contexts = [context]
        self.close_calls = 0

    def is_connected(self) -> bool:
        return True

    async def close(self) -> None:
        self.close_calls += 1


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


class _FakeCdpSession:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.send_calls: list[tuple[str, dict[str, object]]] = []

    async def send(self, method: str, params: dict[str, object]):
        self.send_calls.append((method, params))
        if self.error is not None:
            raise self.error
        return self.result


class _FakePlaywright:
    def __init__(self) -> None:
        self.stop_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1


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


@pytest.mark.asyncio
async def test_browserbase_skips_virtual_mouse_init_script_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeBrowserbase.instances.clear()
    monkeypatch.setattr(browserbase_module, "AsyncBrowserbase", _FakeBrowserbase)

    page = _FakePage()
    browser_context = _FakeBrowserContext(page)
    browser = _FakeBrowser(browser_context)
    chromium = _FakeChromium(browser)

    computer = BrowserbaseBrowser(virtual_mouse=False)
    computer._playwright = SimpleNamespace(chromium=chromium)

    await computer._get_browser_and_page(_make_context())

    assert browser_context.init_scripts == []


@pytest.mark.asyncio
async def test_browserbase_aexit_prints_replay_only_after_cleanup_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(browserbase_module, "AsyncBrowserbase", _FakeBrowserbase)

    page = _FakePage()
    browser_context = _FakeBrowserContext(page)
    browser = _FakeBrowser(browser_context)
    playwright = _FakePlaywright()
    computer = BrowserbaseBrowser()
    computer._page = page
    computer._browser = browser
    computer._playwright = playwright
    computer.session = SimpleNamespace(id="session-cleanup")

    await computer.__aexit__(None, None, None)

    assert page.close_calls == 1
    assert browser.close_calls == 1
    assert playwright.stop_calls == 1
    assert (
        "Session completed. View replay at "
        "https://browserbase.com/sessions/session-cleanup"
    ) in capsys.readouterr().out

    failing_page = _FakePage()
    failing_page.close_error = RuntimeError("close failed")
    failing_browser_context = _FakeBrowserContext(failing_page)
    failing_browser = _FakeBrowser(failing_browser_context)
    failing_playwright = _FakePlaywright()
    failing_computer = BrowserbaseBrowser()
    failing_computer._page = failing_page
    failing_computer._browser = failing_browser
    failing_computer._playwright = failing_playwright
    failing_computer.session = SimpleNamespace(id="session-close-error")

    with pytest.raises(RuntimeError, match="close failed"):
        await failing_computer.__aexit__(None, None, None)

    assert failing_page.close_calls == 1
    assert failing_browser.close_calls == 0
    assert failing_playwright.stop_calls == 0
    assert "Session completed. View replay at" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_browserbase_screenshot_cdp_success_and_failure_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(browserbase_module, "AsyncBrowserbase", _FakeBrowserbase)
    page = _FakePage()
    browser_context = _FakeBrowserContext(page)
    browser = _FakeBrowser(browser_context)
    computer = BrowserbaseBrowser()
    computer._browser = browser
    computer._page = page

    cdp_session = _FakeCdpSession(result={"data": "cdp-base64"})
    browser_context.cdp_session = cdp_session

    assert await computer.screenshot(_make_context()) == "cdp-base64"
    assert cdp_session.send_calls == [
        ("Page.captureScreenshot", {"format": "png", "fromSurface": True})
    ]

    async def fake_base_screenshot(self, context):
        assert self is computer
        assert isinstance(context, ComputerContext)
        return "base-fallback"

    monkeypatch.setattr(BasePlaywrightComputer, "screenshot", fake_base_screenshot)
    cdp_session = _FakeCdpSession(error=PlaywrightError("cdp failed"))
    browser_context.cdp_session = cdp_session
    assert await computer.screenshot(_make_context()) == "base-fallback"
    assert cdp_session.send_calls == [
        ("Page.captureScreenshot", {"format": "png", "fromSurface": True})
    ]

    cdp_session = _FakeCdpSession(error=RuntimeError("boom"))
    browser_context.cdp_session = cdp_session
    with pytest.raises(RuntimeError, match="boom"):
        await computer.screenshot(_make_context())
