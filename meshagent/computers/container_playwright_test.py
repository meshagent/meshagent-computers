from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from playwright.async_api import Error as PlaywrightError

from meshagent.computers import container_playwright as container_playwright_module
from meshagent.computers.container_playwright import ContainerPlaywrightComputer


@pytest.fixture(autouse=True)
def _clear_playwright_dimensions_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MESHAGENT_PLAYWRIGHT_DIMENSIONS", raising=False)
    monkeypatch.delenv(
        "MESHAGENT_PLAYWRIGHT_CONNECT_TIMEOUT_SECONDS",
        raising=False,
    )


def test_container_playwright_uses_preinstalled_playwright_server_command() -> None:
    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)
    assert computer.container_command == (
        '/bin/sh -c "playwright run-server --port 3000 --host 0.0.0.0"'
    )


def test_container_playwright_uses_supported_dimension_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MESHAGENT_PLAYWRIGHT_DIMENSIONS", "1600x900")
    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)
    assert computer.dimensions == (1600, 900)


def test_container_playwright_falls_back_to_default_for_unsupported_dimensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MESHAGENT_PLAYWRIGHT_DIMENSIONS", "1024x768")
    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)
    assert computer.dimensions == (1440, 900)


def test_container_playwright_uses_connect_timeout_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MESHAGENT_PLAYWRIGHT_CONNECT_TIMEOUT_SECONDS", "180")
    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)
    assert computer.connect_timeout_seconds == 180.0


def test_container_playwright_falls_back_to_default_for_invalid_connect_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MESHAGENT_PLAYWRIGHT_CONNECT_TIMEOUT_SECONDS", "invalid")
    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)
    assert (
        computer.connect_timeout_seconds
        == container_playwright_module.PLAYWRIGHT_CONNECT_TIMEOUT_SECONDS
    )


class _FakePage:
    def __init__(self) -> None:
        self.viewport_calls: list[dict[str, int]] = []
        self.goto_calls: list[str] = []

    async def set_viewport_size(self, viewport: dict[str, int]) -> None:
        self.viewport_calls.append(viewport)

    async def goto(self, url: str) -> None:
        self.goto_calls.append(url)


class _FakeBrowser:
    def __init__(
        self,
        *,
        page: _FakePage | None = None,
        new_page_factory=None,
    ) -> None:
        self._page = page
        self._new_page_factory = new_page_factory
        self.close_calls = 0

    async def new_page(self) -> _FakePage:
        if self._new_page_factory is not None:
            return await self._new_page_factory()
        assert self._page is not None
        return self._page

    async def close(self) -> None:
        self.close_calls += 1


class _FakeChromium:
    def __init__(self, *, responses: list[object]) -> None:
        self._responses = list(responses)
        self.connect_calls: list[dict[str, object]] = []

    async def connect(
        self,
        base_url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> object:
        self.connect_calls.append(
            {
                "base_url": base_url,
                "headers": headers,
                "timeout": timeout,
            }
        )
        response = self._responses[
            min(len(self.connect_calls) - 1, len(self._responses) - 1)
        ]
        if isinstance(response, Exception):
            raise response
        return response


class _FakeForwarder:
    def __init__(self, *, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


class _FakeRoom:
    def __init__(self) -> None:
        self.protocol = SimpleNamespace(token="token")
        self.containers = SimpleNamespace()


async def _health_check_ready(*, base_url: str, timeout_seconds: float) -> str:
    del timeout_seconds
    return base_url


@pytest.mark.asyncio
async def test_container_playwright_uses_custom_starting_url() -> None:
    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(
        room=room,
        headless=True,
        starting_url="https://example.com",
    )

    async def _ensure_container() -> str:
        return "container_1"

    async def _base_url(*, container_id: str) -> str:
        assert container_id == "container_1"
        return "ws://127.0.0.1:62000/"

    computer.ensure_container = _ensure_container  # type: ignore[method-assign]
    computer._base_url = _base_url  # type: ignore[method-assign]
    computer._check_server_ready_once = _health_check_ready  # type: ignore[method-assign]

    page = _FakePage()
    browser = _FakeBrowser(page=page)
    chromium = _FakeChromium(responses=[browser])
    computer._playwright = SimpleNamespace(chromium=chromium)

    connected_browser, connected_page = await computer._get_browser_and_page()

    assert connected_browser is browser
    assert connected_page is page
    assert page.viewport_calls == [{"width": 1440, "height": 900}]
    assert page.goto_calls == ["https://example.com"]


@pytest.mark.asyncio
async def test_container_playwright_uses_ws_endpoint_returned_by_health_check() -> None:
    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)

    async def _ensure_container() -> str:
        return "container_1"

    async def _base_url(*, container_id: str) -> str:
        assert container_id == "container_1"
        return "ws://127.0.0.1:62000/"

    async def _health_check(
        *,
        base_url: str,
        timeout_seconds: float,
    ) -> str:
        assert base_url == "ws://127.0.0.1:62000/"
        assert timeout_seconds > 0
        return "ws://127.0.0.1:62000/secret-path"

    computer.ensure_container = _ensure_container  # type: ignore[method-assign]
    computer._base_url = _base_url  # type: ignore[method-assign]
    computer._check_server_ready_once = _health_check  # type: ignore[method-assign]

    page = _FakePage()
    browser = _FakeBrowser(page=page)
    chromium = _FakeChromium(responses=[browser])
    computer._playwright = SimpleNamespace(chromium=chromium)

    connected_browser, connected_page = await computer._get_browser_and_page()

    assert connected_browser is browser
    assert connected_page is page
    assert len(chromium.connect_calls) == 1
    assert chromium.connect_calls[0]["base_url"] == "ws://127.0.0.1:62000/secret-path"
    assert chromium.connect_calls[0]["headers"] == {}
    assert chromium.connect_calls[0]["timeout"] > 0


@pytest.mark.asyncio
async def test_container_playwright_retries_connect_on_port_forward_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MESHAGENT_SESSION_ID", raising=False)
    monkeypatch.delenv("MESHAGENT_TUNNEL_PLAYWRIGHT", raising=False)

    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)
    computer.connect_timeout_seconds = 5.0
    computer.connect_backoff_initial_seconds = 0.1
    computer.connect_backoff_max_seconds = 0.5

    async def _ensure_container() -> str:
        return "container_1"

    computer.ensure_container = _ensure_container  # type: ignore[method-assign]

    sleep_calls: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(container_playwright_module.asyncio, "sleep", _fake_sleep)

    forwarders: list[_FakeForwarder] = []

    async def _fake_port_forward(
        *,
        container_id: str,
        port: int,
        token: str,
    ) -> _FakeForwarder:
        assert container_id == "container_1"
        assert port == 3000
        assert token == "token"
        forwarder = _FakeForwarder(host="127.0.0.1", port=62000 + len(forwarders))
        forwarders.append(forwarder)
        return forwarder

    monkeypatch.setattr(container_playwright_module, "port_forward", _fake_port_forward)
    computer._check_server_ready_once = _health_check_ready  # type: ignore[method-assign]

    restart_calls: list[bool] = []

    async def _restart_playwright_client() -> None:
        restart_calls.append(True)

    computer.restart_playwright_client = _restart_playwright_client  # type: ignore[method-assign]

    page = _FakePage()
    browser = _FakeBrowser(page=page)
    chromium = _FakeChromium(
        responses=[
            PlaywrightError("dial tcp4 127.0.0.1:3000: connect: connection refused"),
            browser,
        ]
    )
    computer._playwright = SimpleNamespace(chromium=chromium)

    connected_browser, connected_page = await computer._get_browser_and_page()

    assert connected_browser is browser
    assert connected_page is page
    assert len(chromium.connect_calls) == 2
    assert chromium.connect_calls[0]["base_url"] == "ws://127.0.0.1:62000/"
    assert chromium.connect_calls[1]["base_url"] == "ws://127.0.0.1:62001/"
    assert restart_calls == [True]
    assert sleep_calls == [0.1]
    assert len(forwarders) == 2
    assert forwarders[0].close_calls == 1
    assert forwarders[1].close_calls == 0
    assert page.viewport_calls == [{"width": 1440, "height": 900}]
    assert page.goto_calls == ["https://google.com"]


@pytest.mark.asyncio
async def test_container_playwright_retry_times_out_with_max_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MESHAGENT_SESSION_ID", raising=False)
    monkeypatch.delenv("MESHAGENT_TUNNEL_PLAYWRIGHT", raising=False)

    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)
    computer.connect_timeout_seconds = 0.0
    computer.connect_backoff_initial_seconds = 0.1
    computer.connect_backoff_max_seconds = 0.5

    async def _ensure_container() -> str:
        return "container_1"

    computer.ensure_container = _ensure_container  # type: ignore[method-assign]

    forwarders: list[_FakeForwarder] = []

    async def _fake_port_forward(
        *,
        container_id: str,
        port: int,
        token: str,
    ) -> _FakeForwarder:
        assert container_id == "container_1"
        assert port == 3000
        assert token == "token"
        forwarder = _FakeForwarder(host="127.0.0.1", port=63000 + len(forwarders))
        forwarders.append(forwarder)
        return forwarder

    monkeypatch.setattr(container_playwright_module, "port_forward", _fake_port_forward)
    computer._check_server_ready_once = _health_check_ready  # type: ignore[method-assign]

    chromium = _FakeChromium(
        responses=[
            PlaywrightError("dial tcp4 127.0.0.1:3000: connect: connection refused"),
        ]
    )
    computer._playwright = SimpleNamespace(chromium=chromium)

    with pytest.raises(
        TimeoutError, match="timed out waiting for playwright websocket endpoint"
    ):
        await computer._get_browser_and_page()

    assert len(chromium.connect_calls) == 0
    assert len(forwarders) == 1
    assert forwarders[0].close_calls == 1


@pytest.mark.asyncio
async def test_container_playwright_retries_when_page_setup_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MESHAGENT_SESSION_ID", raising=False)
    monkeypatch.delenv("MESHAGENT_TUNNEL_PLAYWRIGHT", raising=False)

    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)
    computer.connect_timeout_seconds = 5.0
    computer.connect_attempt_timeout_seconds = 0.01
    computer.connect_backoff_initial_seconds = 0.1
    computer.connect_backoff_max_seconds = 0.5

    async def _ensure_container() -> str:
        return "container_1"

    computer.ensure_container = _ensure_container  # type: ignore[method-assign]

    sleep_calls: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(container_playwright_module.asyncio, "sleep", _fake_sleep)

    forwarders: list[_FakeForwarder] = []

    async def _fake_port_forward(
        *,
        container_id: str,
        port: int,
        token: str,
    ) -> _FakeForwarder:
        assert container_id == "container_1"
        assert port == 3000
        assert token == "token"
        forwarder = _FakeForwarder(host="127.0.0.1", port=63500 + len(forwarders))
        forwarders.append(forwarder)
        return forwarder

    monkeypatch.setattr(container_playwright_module, "port_forward", _fake_port_forward)
    computer._check_server_ready_once = _health_check_ready  # type: ignore[method-assign]

    restart_calls: list[bool] = []

    async def _restart_playwright_client() -> None:
        restart_calls.append(True)

    computer.restart_playwright_client = _restart_playwright_client  # type: ignore[method-assign]

    async def _hang_new_page() -> _FakePage:
        await asyncio.Future()

    stuck_browser = _FakeBrowser(new_page_factory=_hang_new_page)
    page = _FakePage()
    browser = _FakeBrowser(page=page)
    chromium = _FakeChromium(
        responses=[
            stuck_browser,
            browser,
        ]
    )
    computer._playwright = SimpleNamespace(chromium=chromium)

    connected_browser, connected_page = await computer._get_browser_and_page()

    assert connected_browser is browser
    assert connected_page is page
    assert len(chromium.connect_calls) == 2
    assert chromium.connect_calls[0]["base_url"] == "ws://127.0.0.1:63500/"
    assert chromium.connect_calls[1]["base_url"] == "ws://127.0.0.1:63501/"
    assert restart_calls == [True]
    assert sleep_calls == [0.1]
    assert len(forwarders) == 2
    assert forwarders[0].close_calls == 1
    assert forwarders[1].close_calls == 0
    assert stuck_browser.close_calls == 1
    assert page.viewport_calls == [{"width": 1440, "height": 900}]
    assert page.goto_calls == ["https://google.com"]


@pytest.mark.asyncio
async def test_container_playwright_retries_direct_connect_on_connection_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MESHAGENT_SESSION_ID", "session_1")
    monkeypatch.delenv("MESHAGENT_TUNNEL_PLAYWRIGHT", raising=False)

    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)
    computer.connect_timeout_seconds = 5.0
    computer.connect_backoff_initial_seconds = 0.1
    computer.connect_backoff_max_seconds = 0.5

    async def _ensure_container() -> str:
        return "container_1"

    computer.ensure_container = _ensure_container  # type: ignore[method-assign]

    sleep_calls: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(container_playwright_module.asyncio, "sleep", _fake_sleep)

    forwarders: list[_FakeForwarder] = []

    async def _fake_port_forward(
        *,
        container_id: str,
        port: int,
        token: str,
    ) -> _FakeForwarder:
        assert container_id == "container_1"
        assert port == 3000
        assert token == "token"
        forwarder = _FakeForwarder(host="127.0.0.1", port=64000 + len(forwarders))
        forwarders.append(forwarder)
        return forwarder

    monkeypatch.setattr(container_playwright_module, "port_forward", _fake_port_forward)
    computer._check_server_ready_once = _health_check_ready  # type: ignore[method-assign]

    restart_calls: list[bool] = []

    async def _restart_playwright_client() -> None:
        restart_calls.append(True)

    computer.restart_playwright_client = _restart_playwright_client  # type: ignore[method-assign]

    page = _FakePage()
    browser = _FakeBrowser(page=page)
    chromium = _FakeChromium(
        responses=[
            PlaywrightError("connect ECONNREFUSED 127.0.0.1:3000"),
            browser,
        ]
    )
    computer._playwright = SimpleNamespace(chromium=chromium)

    connected_browser, connected_page = await computer._get_browser_and_page()

    assert connected_browser is browser
    assert connected_page is page
    assert len(chromium.connect_calls) == 2
    assert chromium.connect_calls[0]["base_url"] == "ws://127.0.0.1:64000/"
    assert chromium.connect_calls[1]["base_url"] == "ws://127.0.0.1:64001/"
    assert restart_calls == [True]
    assert sleep_calls == [0.1]
    assert len(forwarders) == 2
    assert forwarders[0].close_calls == 1
    assert forwarders[1].close_calls == 0


@pytest.mark.asyncio
async def test_container_playwright_resets_client_after_connect_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MESHAGENT_SESSION_ID", raising=False)
    monkeypatch.delenv("MESHAGENT_TUNNEL_PLAYWRIGHT", raising=False)

    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)
    computer.connect_timeout_seconds = 5.0
    computer.connect_backoff_initial_seconds = 0.1
    computer.connect_backoff_max_seconds = 0.5

    async def _ensure_container() -> str:
        return "container_1"

    computer.ensure_container = _ensure_container  # type: ignore[method-assign]

    sleep_calls: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(container_playwright_module.asyncio, "sleep", _fake_sleep)

    forwarders: list[_FakeForwarder] = []

    async def _fake_port_forward(
        *,
        container_id: str,
        port: int,
        token: str,
    ) -> _FakeForwarder:
        assert container_id == "container_1"
        assert port == 3000
        assert token == "token"
        forwarder = _FakeForwarder(host="127.0.0.1", port=64500 + len(forwarders))
        forwarders.append(forwarder)
        return forwarder

    monkeypatch.setattr(container_playwright_module, "port_forward", _fake_port_forward)
    computer._check_server_ready_once = _health_check_ready  # type: ignore[method-assign]

    restart_calls: list[bool] = []

    async def _restart_playwright_client() -> None:
        restart_calls.append(True)

    computer.restart_playwright_client = _restart_playwright_client  # type: ignore[method-assign]

    page = _FakePage()
    browser = _FakeBrowser(page=page)
    chromium = _FakeChromium(
        responses=[
            PlaywrightError("Connection timed out"),
            browser,
        ]
    )
    computer._playwright = SimpleNamespace(chromium=chromium)

    connected_browser, connected_page = await computer._get_browser_and_page()

    assert connected_browser is browser
    assert connected_page is page
    assert len(chromium.connect_calls) == 2
    assert chromium.connect_calls[0]["base_url"] == "ws://127.0.0.1:64500/"
    assert chromium.connect_calls[1]["base_url"] == "ws://127.0.0.1:64501/"
    assert restart_calls == [True]
    assert sleep_calls == [0.1]
    assert len(forwarders) == 2
    assert forwarders[0].close_calls == 1
    assert forwarders[1].close_calls == 0


@pytest.mark.asyncio
async def test_container_playwright_retries_direct_connect_on_socket_hang_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MESHAGENT_SESSION_ID", "session_1")
    monkeypatch.delenv("MESHAGENT_TUNNEL_PLAYWRIGHT", raising=False)

    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)
    computer.connect_timeout_seconds = 5.0
    computer.connect_backoff_initial_seconds = 0.1
    computer.connect_backoff_max_seconds = 0.5

    async def _ensure_container() -> str:
        return "container_1"

    computer.ensure_container = _ensure_container  # type: ignore[method-assign]

    sleep_calls: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(container_playwright_module.asyncio, "sleep", _fake_sleep)

    forwarders: list[_FakeForwarder] = []

    async def _fake_port_forward(
        *,
        container_id: str,
        port: int,
        token: str,
    ) -> _FakeForwarder:
        assert container_id == "container_1"
        assert port == 3000
        assert token == "token"
        forwarder = _FakeForwarder(host="127.0.0.1", port=65000 + len(forwarders))
        forwarders.append(forwarder)
        return forwarder

    monkeypatch.setattr(container_playwright_module, "port_forward", _fake_port_forward)
    computer._check_server_ready_once = _health_check_ready  # type: ignore[method-assign]

    restart_calls: list[bool] = []

    async def _restart_playwright_client() -> None:
        restart_calls.append(True)

    computer.restart_playwright_client = _restart_playwright_client  # type: ignore[method-assign]

    page = _FakePage()
    browser = _FakeBrowser(page=page)
    chromium = _FakeChromium(
        responses=[
            PlaywrightError(
                "BrowserType.connect: WebSocket error: socket hang up code=1006"
            ),
            browser,
        ]
    )
    computer._playwright = SimpleNamespace(chromium=chromium)

    connected_browser, connected_page = await computer._get_browser_and_page()

    assert connected_browser is browser
    assert connected_page is page
    assert len(chromium.connect_calls) == 2
    assert chromium.connect_calls[0]["base_url"] == "ws://127.0.0.1:65000/"
    assert chromium.connect_calls[1]["base_url"] == "ws://127.0.0.1:65001/"
    assert restart_calls == [True]
    assert sleep_calls == [0.1]
    assert len(forwarders) == 2
    assert forwarders[0].close_calls == 1
    assert forwarders[1].close_calls == 0


@pytest.mark.asyncio
async def test_container_playwright_retries_until_http_health_check_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)
    computer.connect_timeout_seconds = 5.0
    computer.connect_backoff_initial_seconds = 0.1
    computer.connect_backoff_max_seconds = 0.5

    async def _ensure_container() -> str:
        return "container_1"

    computer.ensure_container = _ensure_container  # type: ignore[method-assign]

    sleep_calls: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(container_playwright_module.asyncio, "sleep", _fake_sleep)

    forwarders: list[_FakeForwarder] = []

    async def _fake_port_forward(
        *,
        container_id: str,
        port: int,
        token: str,
    ) -> _FakeForwarder:
        assert container_id == "container_1"
        assert port == 3000
        assert token == "token"
        forwarder = _FakeForwarder(host="127.0.0.1", port=65500 + len(forwarders))
        forwarders.append(forwarder)
        return forwarder

    monkeypatch.setattr(container_playwright_module, "port_forward", _fake_port_forward)

    health_calls: list[tuple[str, float]] = []

    async def _fake_health_check(*, base_url: str, timeout_seconds: float) -> str:
        health_calls.append((base_url, timeout_seconds))
        if len(health_calls) == 1:
            raise ConnectionError("playwright container is still starting")
        return base_url

    computer._check_server_ready_once = _fake_health_check  # type: ignore[method-assign]

    page = _FakePage()
    browser = _FakeBrowser(page=page)
    chromium = _FakeChromium(responses=[browser])
    computer._playwright = SimpleNamespace(chromium=chromium)

    connected_browser, connected_page = await computer._get_browser_and_page()

    assert connected_browser is browser
    assert connected_page is page
    assert [base_url for base_url, _ in health_calls] == [
        "ws://127.0.0.1:65500/",
        "ws://127.0.0.1:65500/",
    ]
    assert all(timeout_seconds > 0 for _, timeout_seconds in health_calls)
    assert len(chromium.connect_calls) == 1
    assert chromium.connect_calls[0]["base_url"] == "ws://127.0.0.1:65500/"
    assert sleep_calls == [0.1]
    assert len(forwarders) == 1
    assert forwarders[0].close_calls == 0
