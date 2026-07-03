import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from dataclasses import asdict

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


class _FakeRemoteBrowserContext:
    def __init__(self, page: _FakePage | None = None) -> None:
        self.pages = [] if page is None else [page]
        self.new_context_calls: list[dict[str, object]] = []
        self.new_page_calls = 0
        self.page = page or _FakePage()

    async def new_page(self) -> _FakePage:
        self.new_page_calls += 1
        self.pages.append(self.page)
        return self.page


class _FakeRemoteBrowser:
    def __init__(self, context: _FakeRemoteBrowserContext | None = None) -> None:
        self.contexts = [] if context is None else [context]
        self.context = context or _FakeRemoteBrowserContext()
        self.new_context_calls: list[dict[str, object]] = []

    async def new_context(self, **kwargs) -> _FakeRemoteBrowserContext:
        self.new_context_calls.append(kwargs)
        self.contexts.append(self.context)
        return self.context


class _FakeRemoteChromium:
    def __init__(self, browser: _FakeRemoteBrowser) -> None:
        self.browser = browser
        self.connect_calls: list[dict[str, object]] = []

    async def connect_over_cdp(self, cdp_url: str, **kwargs):
        self.connect_calls.append({"cdp_url": cdp_url, **kwargs})
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
        starting_url="  https://example.com  ",
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
        ("  https://example.com  ", None),
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


@pytest.mark.asyncio
async def test_stagehand_computer_remote_session_plan_matches_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeStagehand.init_calls.clear()
    remote_browser = _FakeRemoteBrowser()
    chromium = _FakeRemoteChromium(browser=remote_browser)

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
        starting_url="https://remote.example",
        stagehand_config=StagehandComputerConfig(
            server="remote",
            browser={"type": "browserbase"},
        ),
    )
    computer._playwright = SimpleNamespace(chromium=chromium)

    browser, page = await computer._get_browser_and_page(_make_context())

    assert browser is remote_browser
    assert page is remote_browser.context.page
    assert chromium.connect_calls == [
        {
            "cdp_url": "ws://127.0.0.1:9222/devtools/browser/example",
            "timeout": 60_000,
        }
    ]
    assert remote_browser.new_context_calls == [
        {"viewport": {"width": 1600, "height": 900}}
    ]
    assert remote_browser.context.new_page_calls == 1
    assert page.viewport_calls == [{"width": 1600, "height": 900}]
    assert page.goto_calls == [("https://remote.example", None)]

    assert len(_FakeStagehand.init_calls) == 1
    init_call = _FakeStagehand.init_calls[0]
    assert init_call["server"] == "remote"
    assert init_call["model_api_key"] == "room_token"
    assert init_call["local_openai_api_key"] == "room_token"

    stagehand = computer._stagehand
    assert isinstance(stagehand, _FakeStagehand)
    assert stagehand.sessions.start_calls == [
        {
            "model_name": "openai/gpt-5.4",
            "browser": {"type": "browserbase"},
        }
    ]
    assert computer._stagehand_session_id == "stagehand_session_1"


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


def test_stagehand_config_defaults_match_python_dataclass() -> None:
    assert asdict(StagehandComputerConfig()) == {
        "model_name": "openai/gpt-5.4",
        "server": "local",
        "browserbase_api_key": None,
        "browserbase_project_id": None,
        "browser": None,
        "browserbase_session_create_params": None,
        "browserbase_session_id": None,
        "dom_settle_timeout_ms": None,
        "experimental": None,
        "self_heal": None,
        "system_prompt": None,
        "verbose": None,
        "local_host": "127.0.0.1",
        "local_port": 0,
        "local_headless": True,
        "local_chromium_sandbox": False,
        "local_chrome_path": None,
        "local_ready_timeout_s": 30.0,
        "local_shutdown_on_close": True,
        "timeout": None,
        "max_retries": 2,
    }


def test_stagehand_start_kwargs_and_update_conflict_match_python() -> None:
    computer = StagehandComputer(
        stagehand_config=StagehandComputerConfig(
            model_name="openai/custom",
            browserbase_session_create_params={"keepAlive": True},
            browserbase_session_id="session-1",
            dom_settle_timeout_ms=1234.0,
            experimental=True,
            self_heal=False,
            system_prompt="system",
            verbose=2,
        )
    )

    assert computer._runtime_stagehand_start_kwargs(browser={"type": "remote"}) == {
        "model_name": "openai/custom",
        "browser": {"type": "remote"},
        "browserbase_session_create_params": {"keepAlive": True},
        "browserbase_session_id": "session-1",
        "dom_settle_timeout_ms": 1234.0,
        "experimental": True,
        "self_heal": False,
        "system_prompt": "system",
        "verbose": 2,
    }
    assert computer._runtime_stagehand_start_kwargs(browser=None) == {
        "model_name": "openai/custom",
        "browserbase_session_create_params": {"keepAlive": True},
        "browserbase_session_id": "session-1",
        "dom_settle_timeout_ms": 1234.0,
        "experimental": True,
        "self_heal": False,
        "system_prompt": "system",
        "verbose": 2,
    }

    with pytest.raises(ValueError, match="pass config or keyword changes, not both"):
        computer.update_stagehand_config(
            config=StagehandComputerConfig(),
            model_name="openai/other",
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


def test_stagehand_platform_path_and_url_helpers_match_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stagehand_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(stagehand_module.platform, "machine", lambda: "x86_64")
    assert stagehand_module._playwright_host_platform() == "linux-x64"  # noqa: SLF001

    monkeypatch.setattr(stagehand_module.platform, "machine", lambda: "aarch64")
    assert stagehand_module._playwright_host_platform() == "linux-arm64"  # noqa: SLF001

    monkeypatch.setattr(stagehand_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(stagehand_module.platform, "machine", lambda: "arm64")
    assert stagehand_module._playwright_host_platform() == "mac-arm64"  # noqa: SLF001

    monkeypatch.setattr(stagehand_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(stagehand_module.platform, "machine", lambda: "arm64")
    assert stagehand_module._playwright_host_platform() == "win-x64"  # noqa: SLF001

    monkeypatch.setattr(stagehand_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(stagehand_module.platform, "machine", lambda: "riscv64")
    assert stagehand_module._playwright_host_platform() is None  # noqa: SLF001

    monkeypatch.setattr(
        stagehand_module,
        "_PLAYWRIGHT_BROWSERS_JSON_PATH",
        Path("/repo/playwright/driver/package/browsers.json"),
    )
    monkeypatch.setattr(stagehand_module.Path, "home", lambda *args: Path("/home/me"))
    monkeypatch.setenv("HOME", "/home/me")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
    assert stagehand_module._playwright_registry_directory() == Path(  # noqa: SLF001
        "/repo/playwright/.local-browsers"
    )

    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "~/pw")
    assert stagehand_module._playwright_registry_directory() == Path(  # noqa: SLF001
        "/home/me/pw"
    )

    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", "~/cache")
    assert stagehand_module._playwright_registry_directory() == Path(  # noqa: SLF001
        "/home/me/cache/ms-playwright"
    )

    monkeypatch.setattr(stagehand_module.platform, "system", lambda: "Darwin")
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    assert stagehand_module._playwright_registry_directory() == Path(  # noqa: SLF001
        "/home/me/Library/Caches/ms-playwright"
    )

    monkeypatch.setattr(stagehand_module.platform, "system", lambda: "Plan9")
    with pytest.raises(
        RuntimeError,
        match="unsupported platform for Playwright registry: Plan9",
    ):
        stagehand_module._playwright_registry_directory()  # noqa: SLF001

    monkeypatch.setenv("CHROME_PATH", "/env/chrome")
    assert (  # noqa: SLF001
        stagehand_module._effective_local_chrome_path(
            local_chrome_path=" /custom/chrome "
        )
        == " /custom/chrome "
    )
    assert (  # noqa: SLF001
        stagehand_module._effective_local_chrome_path(local_chrome_path=" ")
        == "/env/chrome"
    )

    local_headless_names: list[str] = []

    def fake_managed_browser_path(*, browser_name: str):
        local_headless_names.append(browser_name)
        return None

    monkeypatch.delenv("CHROME_PATH", raising=False)
    monkeypatch.setattr(
        stagehand_module,
        "_playwright_managed_browser_executable_path",
        fake_managed_browser_path,
    )
    assert (
        stagehand_module._playwright_local_browser_available(  # noqa: SLF001
            local_headless=True,
            local_chrome_path=None,
        )
        is False
    )
    assert (
        stagehand_module._playwright_local_browser_available(  # noqa: SLF001
            local_headless=False,
            local_chrome_path=None,
        )
        is False
    )
    assert local_headless_names == ["chromium-headless-shell", "chromium"]

    room = _FakeRoom()
    room.room_url = "ws://localhost:8080/rooms/test/"
    assert (
        stagehand_module._room_openai_base_url(room=room)  # noqa: SLF001
        == "http://localhost:8080/rooms/test/openai/v1"
    )
    room.room_url = "wss://example.test/rooms/test"
    assert (
        stagehand_module._room_openai_base_url(room=room)  # noqa: SLF001
        == "https://example.test/rooms/test/openai/v1"
    )


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
