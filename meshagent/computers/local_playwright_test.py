import pytest

from meshagent.computers.computer import ComputerContext
from meshagent.computers.local_playwright import LocalPlaywrightComputer


class _FakeParticipant:
    def get_attribute(self, key: str):
        del key
        return None


class _FakeRoom:
    local_participant = _FakeParticipant()


class _FakePage:
    def __init__(self) -> None:
        self.viewport_calls: list[dict[str, int]] = []
        self.goto_calls: list[str] = []

    async def set_viewport_size(self, viewport: dict[str, int]) -> None:
        self.viewport_calls.append(viewport)

    async def goto(self, url: str) -> None:
        self.goto_calls.append(url)


class _FakeBrowser:
    def __init__(self, page: _FakePage) -> None:
        self.page = page
        self.new_page_calls = 0

    async def new_page(self) -> _FakePage:
        self.new_page_calls += 1
        return self.page


class _FakeChromium:
    def __init__(self, browser: _FakeBrowser) -> None:
        self.browser = browser
        self.launch_calls: list[dict[str, object]] = []

    async def launch(self, **kwargs):
        self.launch_calls.append(kwargs)
        return self.browser


def _make_context() -> ComputerContext:
    room = _FakeRoom()
    return ComputerContext(room=room, caller=room.local_participant)


@pytest.mark.asyncio
async def test_local_playwright_get_browser_and_page_matches_python_launch_shape() -> (
    None
):
    page = _FakePage()
    browser = _FakeBrowser(page)
    chromium = _FakeChromium(browser)
    computer = LocalPlaywrightComputer(
        headless=True,
        dimensions=(1600, 900),
        starting_url=" https://local.test ",
    )
    computer._playwright = type("FakePlaywright", (), {"chromium": chromium})()

    returned_browser, returned_page = await computer._get_browser_and_page(
        _make_context()
    )

    assert returned_browser is browser
    assert returned_page is page
    assert chromium.launch_calls == [
        {
            "chromium_sandbox": True,
            "headless": True,
            "args": [
                "--window-size=1600,900",
                "--disable-extensions",
                "--disable-file-system",
            ],
            "env": {},
        }
    ]
    assert browser.new_page_calls == 1
    assert page.viewport_calls == [{"width": 1600, "height": 900}]
    assert page.goto_calls == [" https://local.test "]
