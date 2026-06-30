import os
from types import SimpleNamespace

import pytest

from meshagent.computers import stagehand as stagehand_module
from meshagent.computers.computer import ComputerContext
from meshagent.computers.stagehand import StagehandComputer, StagehandComputerConfig


class _FakeParticipant:
    def get_attribute(self, key: str):
        del key
        return None


class _FakeProtocol:
    def __init__(self, *, token: str):
        self.token = token


class _FakeRoom:
    def __init__(self) -> None:
        self.protocol = _FakeProtocol(token="room_token")
        self.room_url = "http://localhost:8080/rooms/test-room"
        self.session_id = "session_1"
        self.local_participant = _FakeParticipant()


class _FakePage:
    def __init__(self) -> None:
        self.viewport_calls: list[dict[str, int]] = []
        self.goto_calls: list[tuple[str, str | None]] = []

    async def set_viewport_size(self, viewport: dict[str, int]) -> None:
        self.viewport_calls.append(viewport)

    async def goto(self, url: str, wait_until: str | None = None) -> None:
        self.goto_calls.append((url, wait_until))


class _FakeBrowser:
    def __init__(self, page: _FakePage) -> None:
        self.page = page
        self.close_calls = 0

    async def new_page(self) -> _FakePage:
        return self.page

    async def close(self) -> None:
        self.close_calls += 1


class _FakeChromium:
    def __init__(self, browser: _FakeBrowser) -> None:
        self.browser = browser
        self.launch_calls: list[dict[str, object]] = []

    async def launch(self, **kwargs):
        self.launch_calls.append(kwargs)
        return self.browser


class _FakeStagehandSessions:
    def __init__(self) -> None:
        self.start_calls: list[dict[str, object]] = []
        self.end_calls: list[str] = []
        self.start_env_calls: list[dict[str, str | None]] = []

    async def start(self, **kwargs):
        self.start_calls.append(kwargs)
        self.start_env_calls.append(
            {
                "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL"),
                "MESHAGENT_SESSION_ID": os.environ.get("MESHAGENT_SESSION_ID"),
            }
        )
        return SimpleNamespace(
            data=SimpleNamespace(
                session_id="stagehand_session_1",
                cdp_url="ws://127.0.0.1:9222/devtools/browser/example",
            )
        )

    async def end(self, session_id: str):
        self.end_calls.append(session_id)


class _FakeStagehand:
    init_calls: list[dict[str, object]] = []

    def __init__(self, **kwargs):
        _FakeStagehand.init_calls.append(kwargs)
        self.sessions = _FakeStagehandSessions()
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _make_context() -> ComputerContext:
    room = _FakeRoom()
    return ComputerContext(
        room=room,
        caller=room.local_participant,
    )


@pytest.mark.asyncio
async def test_stagehand_computer_uses_room_runtime_for_local_stagehand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeStagehand.init_calls.clear()
    page = _FakePage()
    browser = _FakeBrowser(page=page)
    chromium = _FakeChromium(browser=browser)

    monkeypatch.setattr(
        stagehand_module, "_require_stagehand_class", lambda: _FakeStagehand
    )
    monkeypatch.setattr(
        stagehand_module,
        "stagehand_available",
        lambda **_: True,
    )

    computer = StagehandComputer(
        dimensions=(1600, 900),
        starting_url="https://example.com",
    )
    computer._playwright = SimpleNamespace(chromium=chromium)

    async def _fake_resolve_cdp_ws_url(*, port: int) -> str:
        assert isinstance(port, int)
        return "ws://127.0.0.1:9222/devtools/browser/example"

    computer._resolve_cdp_ws_url = _fake_resolve_cdp_ws_url  # type: ignore[method-assign]

    browser, page = await computer._get_browser_and_page(_make_context())

    assert browser is chromium.browser
    assert page is chromium.browser.page
    assert len(chromium.launch_calls) == 1
    launch_call = chromium.launch_calls[0]
    assert launch_call["headless"] is True
    assert launch_call["chromium_sandbox"] is False
    assert any(
        isinstance(arg, str) and arg.startswith("--remote-debugging-port=")
        for arg in launch_call["args"]
    )
    assert page.viewport_calls == [{"width": 1600, "height": 900}]
    assert page.goto_calls == [
        ("about:blank", "domcontentloaded"),
        ("https://example.com", None),
    ]

    assert len(_FakeStagehand.init_calls) == 1
    init_call = _FakeStagehand.init_calls[0]
    assert init_call["server"] == "local"
    assert init_call["model_api_key"] == "room_token"
    assert init_call["local_openai_api_key"] == "room_token"

    stagehand = computer._stagehand
    assert isinstance(stagehand, _FakeStagehand)
    assert len(stagehand.sessions.start_calls) == 1
    start_call = stagehand.sessions.start_calls[0]
    assert start_call["model_name"] == "openai/gpt-5.4"
    assert start_call["browser"]["type"] == "local"
    assert (
        start_call["browser"]["launchOptions"]["cdpUrl"]
        == "ws://127.0.0.1:9222/devtools/browser/example"
    )
    assert start_call["browser"]["launchOptions"]["viewport"] == {
        "width": 1600,
        "height": 900,
    }
    assert stagehand.sessions.start_env_calls == [
        {
            "OPENAI_BASE_URL": "http://localhost:8080/rooms/test-room/openai/v1",
            "MESHAGENT_SESSION_ID": "session_1",
        }
    ]
    assert os.environ.get("OPENAI_BASE_URL") is None
    assert os.environ.get("MESHAGENT_SESSION_ID") is None

    await computer.__aexit__(None, None, None)
    assert stagehand.sessions.end_calls == ["stagehand_session_1"]
    assert stagehand.closed is True


def test_stagehand_computer_can_update_config() -> None:
    computer = StagehandComputer()

    computer.update_stagehand_config(
        model_name="openai/gpt-4.1",
        local_headless=False,
    )

    assert computer.stagehand_config == StagehandComputerConfig(
        model_name="openai/gpt-4.1",
        local_headless=False,
    )


def test_stagehand_local_browser_config_uses_python_dict_coercion() -> None:
    computer = StagehandComputer(
        stagehand_config=StagehandComputerConfig(
            browser=[
                ["metadata", "kept"],
                ["launchOptions", {"headless": False, "cdpUrl": "old"}],
            ],  # type: ignore[arg-type]
        )
    )

    assert computer._default_local_browser_config(cdp_url="ws://127.0.0.1:1") == {
        "type": "local",
        "metadata": "kept",
        "launchOptions": {
            "cdpUrl": "ws://127.0.0.1:1",
            "headless": False,
            "viewport": {"width": 1440, "height": 900},
        },
    }

    string_pair = StagehandComputer(
        stagehand_config=StagehandComputerConfig(
            browser=["ab"],  # type: ignore[arg-type]
        )
    )
    assert string_pair._default_local_browser_config(cdp_url="ws://127.0.0.1:2") == {
        "a": "b",
        "type": "local",
        "launchOptions": {
            "cdpUrl": "ws://127.0.0.1:2",
            "headless": True,
            "viewport": {"width": 1440, "height": 900},
        },
    }

    malformed = StagehandComputer(
        stagehand_config=StagehandComputerConfig(
            browser=["abc"],  # type: ignore[arg-type]
        )
    )
    with pytest.raises(
        ValueError,
        match="dictionary update sequence element #0 has length 3; 2 is required",
    ):
        malformed._default_local_browser_config(cdp_url="ws://127.0.0.1:3")

    non_iterable = StagehandComputer(
        stagehand_config=StagehandComputerConfig(
            browser=1,  # type: ignore[arg-type]
        )
    )
    with pytest.raises(TypeError, match="'int' object is not iterable"):
        non_iterable._default_local_browser_config(cdp_url="ws://127.0.0.1:4")


def test_stagehand_available_requires_local_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        stagehand_module,
        "_stagehand_sea_binary_available",
        lambda: True,
    )
    monkeypatch.setattr(
        stagehand_module,
        "_playwright_local_browser_available",
        lambda *, local_headless, local_chrome_path: (
            local_headless is True and local_chrome_path == "/tmp/browser"
        ),
    )

    assert (
        stagehand_module.stagehand_available(
            local_headless=True,
            local_chrome_path="/tmp/browser",
        )
        is True
    )
    assert (
        stagehand_module.stagehand_available(
            local_headless=True,
            local_chrome_path="/tmp/missing-browser",
        )
        is False
    )


def test_stagehand_available_requires_sea_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        stagehand_module,
        "_stagehand_sea_binary_available",
        lambda: False,
    )
    monkeypatch.setattr(
        stagehand_module,
        "_playwright_local_browser_available",
        lambda *, local_headless, local_chrome_path: True,
    )

    assert (
        stagehand_module.stagehand_available(
            local_headless=True,
            local_chrome_path="/tmp/browser",
        )
        is False
    )
