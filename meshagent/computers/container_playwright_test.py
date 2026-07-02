from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from playwright.async_api import Error as PlaywrightError

from meshagent.computers import container_playwright as container_playwright_module
from meshagent.computers.computer import ComputerContext
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
    assert computer.connect_timeout_seconds is None


def test_container_playwright_defaults_to_retrying_without_deadline() -> None:
    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)
    assert computer.connect_timeout_seconds is None


def test_container_playwright_nonpositive_connect_timeout_disables_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MESHAGENT_PLAYWRIGHT_CONNECT_TIMEOUT_SECONDS", "0")
    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)
    assert computer.connect_timeout_seconds is None


@pytest.mark.asyncio
async def test_container_playwright_health_check_requires_ws_endpoint_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)

    writer = _FakeHealthCheckWriter()

    async def _fake_open_connection(*, host: str, port: int):
        assert host == "127.0.0.1"
        assert port == 62000
        return (
            _FakeHealthCheckReader(
                status_line=b"HTTP/1.1 200 OK\r\n",
                headers=[b"Content-Type: application/json\r\n"],
                body=b"{}",
            ),
            writer,
        )

    monkeypatch.setattr(
        container_playwright_module.asyncio,
        "open_connection",
        _fake_open_connection,
    )

    with pytest.raises(
        ConnectionError, match="playwright websocket endpoint is not ready yet"
    ):
        await computer._check_server_ready_once(
            base_url="ws://127.0.0.1:62000/",
            timeout_seconds=1.0,
        )

    assert writer.closed is True
    assert writer.wait_closed_calls == 1


@pytest.mark.asyncio
async def test_container_playwright_websocket_check_requires_upgrade_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)

    writer = _FakeWebSocketHandshakeWriter()

    async def _fake_open_connection(*, host: str, port: int):
        assert host == "127.0.0.1"
        assert port == 62000
        return (
            _FakeWebSocketHandshakeReader(
                status_line=b"HTTP/1.1 200 OK\r\n",
                headers=[b"Content-Type: text/plain\r\n"],
            ),
            writer,
        )

    monkeypatch.setattr(
        container_playwright_module.asyncio,
        "open_connection",
        _fake_open_connection,
    )

    with pytest.raises(
        ConnectionError, match="playwright websocket endpoint is not ready yet"
    ):
        await computer._check_websocket_ready_once(
            ws_url="ws://127.0.0.1:62000/secret-path",
            timeout_seconds=1.0,
        )

    assert writer.closed is True
    assert writer.wait_closed_calls == 1


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
        connected: bool = True,
    ) -> None:
        self._page = page
        self._new_page_factory = new_page_factory
        self._connected = connected
        self.close_calls = 0

    async def new_page(self) -> _FakePage:
        if self._new_page_factory is not None:
            return await self._new_page_factory()
        assert self._page is not None
        return self._page

    def is_connected(self) -> bool:
        return self._connected

    async def close(self) -> None:
        self.close_calls += 1
        self._connected = False


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


class _StuckForwarder(_FakeForwarder):
    def __init__(self, *, host: str, port: int) -> None:
        super().__init__(host=host, port=port)
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.task: asyncio.Task[object] | None = None

    async def close(self) -> None:
        self.close_calls += 1
        self.task = asyncio.current_task()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled.set()
            await self.release.wait()
            raise


class _FakeRoom:
    def __init__(self) -> None:
        self.protocol = SimpleNamespace(token="token")
        self.containers = SimpleNamespace()


class _FakeHealthCheckReader:
    def __init__(
        self, *, status_line: bytes, headers: list[bytes], body: bytes
    ) -> None:
        self._lines = [status_line, *headers, b"\r\n"]
        self._body = body

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""

    async def read(self) -> bytes:
        return self._body


class _FakeHealthCheckWriter:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.closed = False
        self.wait_closed_calls = 0

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        self.wait_closed_calls += 1


class _FakeWebSocketHandshakeReader:
    def __init__(self, *, status_line: bytes, headers: list[bytes]) -> None:
        self._lines = [status_line, *headers, b"\r\n"]

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""


class _FakeWebSocketHandshakeWriter:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.closed = False
        self.wait_closed_calls = 0

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        self.wait_closed_calls += 1


def _make_context() -> ComputerContext:
    return ComputerContext(
        room=object(),  # type: ignore[arg-type]
        caller=object(),  # type: ignore[arg-type]
    )


class _FakeLoop:
    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now


async def _health_check_ready(*, base_url: str, timeout_seconds: float) -> str:
    del timeout_seconds
    return base_url


async def _fake_websocket_check_ready(*, ws_url: str, timeout_seconds: float) -> None:
    del ws_url, timeout_seconds


@pytest.mark.asyncio
async def test_container_playwright_uses_custom_starting_url() -> None:
    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(
        room=room,
        headless=True,
        starting_url="  https://example.com  ",
    )

    async def _ensure_container(context: ComputerContext) -> str:
        del context
        return "container_1"

    async def _base_url(*, container_id: str) -> str:
        assert container_id == "container_1"
        return "ws://127.0.0.1:62000/"

    computer.ensure_container_with_context = _ensure_container  # type: ignore[method-assign]
    computer._base_url = _base_url  # type: ignore[method-assign]
    computer._check_server_ready_once = _health_check_ready  # type: ignore[method-assign]
    computer._check_websocket_ready_once = _fake_websocket_check_ready  # type: ignore[method-assign]

    page = _FakePage()
    browser = _FakeBrowser(page=page)
    chromium = _FakeChromium(responses=[browser])
    computer._playwright = SimpleNamespace(chromium=chromium)

    connected_browser, connected_page = await computer._get_browser_and_page(
        _make_context()
    )

    assert connected_browser is browser
    assert connected_page is page
    assert page.viewport_calls == [{"width": 1440, "height": 900}]
    assert page.goto_calls == ["  https://example.com  "]


@pytest.mark.asyncio
async def test_container_playwright_uses_ws_endpoint_returned_by_health_check() -> None:
    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)

    async def _ensure_container(context: ComputerContext) -> str:
        del context
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

    computer.ensure_container_with_context = _ensure_container  # type: ignore[method-assign]
    computer._base_url = _base_url  # type: ignore[method-assign]
    computer._check_server_ready_once = _health_check  # type: ignore[method-assign]
    computer._check_websocket_ready_once = _fake_websocket_check_ready  # type: ignore[method-assign]

    page = _FakePage()
    browser = _FakeBrowser(page=page)
    chromium = _FakeChromium(responses=[browser])
    computer._playwright = SimpleNamespace(chromium=chromium)

    connected_browser, connected_page = await computer._get_browser_and_page(
        _make_context()
    )

    assert connected_browser is browser
    assert connected_page is page
    assert len(chromium.connect_calls) == 1
    assert chromium.connect_calls[0]["base_url"] == "ws://127.0.0.1:62000/secret-path"
    assert chromium.connect_calls[0]["headers"] == {}
    assert chromium.connect_calls[0]["timeout"] == 0


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

    async def _ensure_container(context: ComputerContext) -> str:
        del context
        return "container_1"

    computer.ensure_container_with_context = _ensure_container  # type: ignore[method-assign]

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
    computer._check_websocket_ready_once = _fake_websocket_check_ready  # type: ignore[method-assign]

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

    connected_browser, connected_page = await computer._get_browser_and_page(
        _make_context()
    )

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
    computer.connect_timeout_seconds = 0.05
    computer.connect_backoff_initial_seconds = 0.1
    computer.connect_backoff_max_seconds = 0.5

    async def _ensure_container(context: ComputerContext) -> str:
        del context
        return "container_1"

    computer.ensure_container_with_context = _ensure_container  # type: ignore[method-assign]

    fake_loop = _FakeLoop()
    monkeypatch.setattr(
        container_playwright_module.asyncio,
        "get_running_loop",
        lambda: fake_loop,
    )

    async def _fake_sleep(seconds: float) -> None:
        fake_loop.now += seconds

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
        forwarder = _FakeForwarder(host="127.0.0.1", port=63000 + len(forwarders))
        forwarders.append(forwarder)
        return forwarder

    monkeypatch.setattr(container_playwright_module, "port_forward", _fake_port_forward)
    computer._check_server_ready_once = _health_check_ready  # type: ignore[method-assign]
    computer._check_websocket_ready_once = _fake_websocket_check_ready  # type: ignore[method-assign]

    restart_calls: list[bool] = []

    async def _restart_playwright_client() -> None:
        restart_calls.append(True)

    computer.restart_playwright_client = _restart_playwright_client  # type: ignore[method-assign]

    chromium = _FakeChromium(
        responses=[
            PlaywrightError("dial tcp4 127.0.0.1:3000: connect: connection refused"),
        ]
    )
    computer._playwright = SimpleNamespace(chromium=chromium)

    with pytest.raises(
        TimeoutError, match="timed out waiting for playwright websocket endpoint"
    ):
        await computer._get_browser_and_page(_make_context())

    assert len(chromium.connect_calls) == 1
    assert restart_calls == [True]
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

    async def _ensure_container(context: ComputerContext) -> str:
        del context
        return "container_1"

    computer.ensure_container_with_context = _ensure_container  # type: ignore[method-assign]

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
    computer._check_websocket_ready_once = _fake_websocket_check_ready  # type: ignore[method-assign]

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

    connected_browser, connected_page = await computer._get_browser_and_page(
        _make_context()
    )

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
async def test_container_playwright_retries_when_websocket_connect_hangs(
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

    async def _ensure_container(context: ComputerContext) -> str:
        del context
        return "container_1"

    computer.ensure_container_with_context = _ensure_container  # type: ignore[method-assign]

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
        forwarder = _FakeForwarder(host="127.0.0.1", port=63750 + len(forwarders))
        forwarders.append(forwarder)
        return forwarder

    monkeypatch.setattr(container_playwright_module, "port_forward", _fake_port_forward)
    computer._check_server_ready_once = _health_check_ready  # type: ignore[method-assign]
    computer._check_websocket_ready_once = _fake_websocket_check_ready  # type: ignore[method-assign]

    restart_calls: list[bool] = []

    async def _restart_playwright_client() -> None:
        restart_calls.append(True)

    computer.restart_playwright_client = _restart_playwright_client  # type: ignore[method-assign]

    page = _FakePage()
    browser = _FakeBrowser(page=page)

    class _HangingThenReadyChromium:
        def __init__(self) -> None:
            self.connect_calls: list[dict[str, object]] = []
            self._attempt = 0
            self.first_connect_cancelled = asyncio.Event()
            self.release_first_connect = asyncio.Event()
            self.first_connect_task: asyncio.Task[object] | None = None

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
            self._attempt += 1
            if self._attempt == 1:
                self.first_connect_task = asyncio.current_task()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    self.first_connect_cancelled.set()
                    await self.release_first_connect.wait()
                    raise PlaywrightError("Connection timed out")
            return browser

    chromium = _HangingThenReadyChromium()
    computer._playwright = SimpleNamespace(chromium=chromium)

    connected_browser, connected_page = await computer._get_browser_and_page(
        _make_context()
    )

    assert connected_browser is browser
    assert connected_page is page
    assert chromium.connect_calls[0]["timeout"] == 0
    assert chromium.first_connect_cancelled.is_set()
    assert len(chromium.connect_calls) == 2
    assert chromium.connect_calls[0]["base_url"] == "ws://127.0.0.1:63750/"
    assert chromium.connect_calls[1]["base_url"] == "ws://127.0.0.1:63751/"
    assert chromium.connect_calls[1]["timeout"] == 0
    assert restart_calls == [True]
    assert sleep_calls == [0.1]
    assert len(forwarders) == 2
    assert forwarders[0].close_calls == 1
    assert forwarders[1].close_calls == 0
    assert page.viewport_calls == [{"width": 1440, "height": 900}]
    assert page.goto_calls == ["https://google.com"]
    chromium.release_first_connect.set()
    assert chromium.first_connect_task is not None
    await asyncio.wait_for(
        asyncio.gather(chromium.first_connect_task, return_exceptions=True),
        timeout=1.0,
    )


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

    async def _ensure_container(context: ComputerContext) -> str:
        del context
        return "container_1"

    computer.ensure_container_with_context = _ensure_container  # type: ignore[method-assign]

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
    computer._check_websocket_ready_once = _fake_websocket_check_ready  # type: ignore[method-assign]

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

    connected_browser, connected_page = await computer._get_browser_and_page(
        _make_context()
    )

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

    async def _ensure_container(context: ComputerContext) -> str:
        del context
        return "container_1"

    computer.ensure_container_with_context = _ensure_container  # type: ignore[method-assign]

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
    computer._check_websocket_ready_once = _fake_websocket_check_ready  # type: ignore[method-assign]

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

    connected_browser, connected_page = await computer._get_browser_and_page(
        _make_context()
    )

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
async def test_container_playwright_reset_does_not_block_on_stuck_forwarder_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)
    stuck_forwarder = _StuckForwarder(host="127.0.0.1", port=64599)
    computer._forwarder = stuck_forwarder

    restart_calls: list[bool] = []

    async def _restart_playwright_client() -> None:
        restart_calls.append(True)

    computer.restart_playwright_client = _restart_playwright_client  # type: ignore[method-assign]
    monkeypatch.setattr(
        container_playwright_module,
        "PLAYWRIGHT_FORWARDER_CLOSE_TIMEOUT_SECONDS",
        0.01,
    )

    await computer._reset_after_connect_failure()

    assert restart_calls == [True]
    assert computer._forwarder is None
    assert stuck_forwarder.close_calls == 1
    assert stuck_forwarder.cancelled.is_set()

    stuck_forwarder.release.set()
    if stuck_forwarder.task is not None:
        await asyncio.wait_for(
            asyncio.gather(stuck_forwarder.task, return_exceptions=True),
            timeout=1.0,
        )


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

    async def _ensure_container(context: ComputerContext) -> str:
        del context
        return "container_1"

    computer.ensure_container_with_context = _ensure_container  # type: ignore[method-assign]

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
    computer._check_websocket_ready_once = _fake_websocket_check_ready  # type: ignore[method-assign]

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

    connected_browser, connected_page = await computer._get_browser_and_page(
        _make_context()
    )

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

    async def _ensure_container(context: ComputerContext) -> str:
        del context
        return "container_1"

    computer.ensure_container_with_context = _ensure_container  # type: ignore[method-assign]

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
    computer._check_websocket_ready_once = _fake_websocket_check_ready  # type: ignore[method-assign]
    computer._check_websocket_ready_once = _fake_websocket_check_ready  # type: ignore[method-assign]

    page = _FakePage()
    browser = _FakeBrowser(page=page)
    chromium = _FakeChromium(responses=[browser])
    computer._playwright = SimpleNamespace(chromium=chromium)

    connected_browser, connected_page = await computer._get_browser_and_page(
        _make_context()
    )

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


@pytest.mark.asyncio
async def test_container_playwright_emits_startup_progress_events() -> None:
    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)

    async def _ensure_container(context: ComputerContext) -> str:
        del context
        return "container_1"

    async def _base_url(*, container_id: str) -> str:
        assert container_id == "container_1"
        return "ws://127.0.0.1:62000/"

    health_calls = 0

    async def _fake_health_check(*, base_url: str, timeout_seconds: float) -> str:
        del timeout_seconds
        nonlocal health_calls
        health_calls += 1
        if health_calls == 1:
            raise ConnectionError("playwright container is still starting")
        return base_url

    computer.ensure_container_with_context = _ensure_container  # type: ignore[method-assign]
    computer._base_url = _base_url  # type: ignore[method-assign]
    computer._check_server_ready_once = _fake_health_check  # type: ignore[method-assign]
    computer._check_websocket_ready_once = _fake_websocket_check_ready  # type: ignore[method-assign]

    page = _FakePage()
    browser = _FakeBrowser(page=page)
    chromium = _FakeChromium(responses=[browser])
    computer._playwright = SimpleNamespace(chromium=chromium)

    events: list[dict[str, object]] = []
    context = ComputerContext(
        room=room,  # type: ignore[arg-type]
        caller=SimpleNamespace(id="caller"),
        event_handler=events.append,
        startup_event_factory=lambda state, details: {
            "state": state,
            "details": list(details),
        },
    )

    await computer._get_browser_and_page(context)

    assert events[0] == {
        "state": "in_progress",
        "details": ["Waiting for Playwright container to become ready."],
    }
    assert {
        "state": "in_progress",
        "details": ["Waiting for Playwright browser session to finish initializing."],
    } in events
    assert {
        "state": "in_progress",
        "details": ["Connecting to Playwright browser session."],
    } in events
    assert events[-1] == {
        "state": "completed",
        "details": ["Playwright browser session ready."],
    }


@pytest.mark.asyncio
async def test_container_playwright_retries_until_websocket_handshake_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MESHAGENT_SESSION_ID", raising=False)
    monkeypatch.delenv("MESHAGENT_TUNNEL_PLAYWRIGHT", raising=False)

    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)
    computer.connect_timeout_seconds = 5.0
    computer.connect_backoff_initial_seconds = 0.1
    computer.connect_backoff_max_seconds = 0.5

    async def _ensure_container(context: ComputerContext) -> str:
        del context
        return "container_1"

    computer.ensure_container_with_context = _ensure_container  # type: ignore[method-assign]

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
        forwarder = _FakeForwarder(host="127.0.0.1", port=65600 + len(forwarders))
        forwarders.append(forwarder)
        return forwarder

    monkeypatch.setattr(container_playwright_module, "port_forward", _fake_port_forward)

    async def _fake_health_check(*, base_url: str, timeout_seconds: float) -> str:
        assert timeout_seconds > 0
        return "ws://127.0.0.1:65600/secret-path"

    handshake_calls: list[tuple[str, float]] = []

    async def _fake_websocket_check(*, ws_url: str, timeout_seconds: float) -> None:
        handshake_calls.append((ws_url, timeout_seconds))
        if len(handshake_calls) == 1:
            raise ConnectionError("playwright websocket endpoint is not ready yet")

    computer._check_server_ready_once = _fake_health_check  # type: ignore[method-assign]
    computer._check_websocket_ready_once = _fake_websocket_check_ready  # type: ignore[method-assign]
    computer._check_websocket_ready_once = _fake_websocket_check  # type: ignore[method-assign]

    page = _FakePage()
    browser = _FakeBrowser(page=page)
    chromium = _FakeChromium(responses=[browser])
    computer._playwright = SimpleNamespace(chromium=chromium)

    connected_browser, connected_page = await computer._get_browser_and_page(
        _make_context()
    )

    assert connected_browser is browser
    assert connected_page is page
    assert [ws_url for ws_url, _ in handshake_calls] == [
        "ws://127.0.0.1:65600/secret-path",
        "ws://127.0.0.1:65600/secret-path",
    ]
    assert all(timeout_seconds > 0 for _, timeout_seconds in handshake_calls)
    assert len(chromium.connect_calls) == 1
    assert chromium.connect_calls[0]["base_url"] == "ws://127.0.0.1:65600/secret-path"
    assert sleep_calls == [0.1]
    assert len(forwarders) == 1
    assert forwarders[0].close_calls == 0


@pytest.mark.asyncio
async def test_container_playwright_retries_until_ws_endpoint_path_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MESHAGENT_SESSION_ID", raising=False)
    monkeypatch.delenv("MESHAGENT_TUNNEL_PLAYWRIGHT", raising=False)

    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)
    computer.connect_timeout_seconds = 5.0
    computer.connect_backoff_initial_seconds = 0.1
    computer.connect_backoff_max_seconds = 0.5

    async def _ensure_container(context: ComputerContext) -> str:
        del context
        return "container_1"

    computer.ensure_container_with_context = _ensure_container  # type: ignore[method-assign]

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
        forwarder = _FakeForwarder(host="127.0.0.1", port=65600 + len(forwarders))
        forwarders.append(forwarder)
        return forwarder

    monkeypatch.setattr(container_playwright_module, "port_forward", _fake_port_forward)

    health_calls: list[tuple[str, float]] = []

    async def _fake_health_check(*, base_url: str, timeout_seconds: float) -> str:
        health_calls.append((base_url, timeout_seconds))
        if len(health_calls) == 1:
            raise ConnectionError("playwright websocket endpoint is not ready yet")
        return "ws://127.0.0.1:65600/secret-path"

    computer._check_server_ready_once = _fake_health_check  # type: ignore[method-assign]
    computer._check_websocket_ready_once = _fake_websocket_check_ready  # type: ignore[method-assign]

    page = _FakePage()
    browser = _FakeBrowser(page=page)
    chromium = _FakeChromium(responses=[browser])
    computer._playwright = SimpleNamespace(chromium=chromium)

    connected_browser, connected_page = await computer._get_browser_and_page(
        _make_context()
    )

    assert connected_browser is browser
    assert connected_page is page
    assert [base_url for base_url, _ in health_calls] == [
        "ws://127.0.0.1:65600/",
        "ws://127.0.0.1:65600/",
    ]
    assert all(timeout_seconds > 0 for _, timeout_seconds in health_calls)
    assert len(chromium.connect_calls) == 1
    assert chromium.connect_calls[0]["base_url"] == "ws://127.0.0.1:65600/secret-path"
    assert sleep_calls == [0.1]
    assert len(forwarders) == 1
    assert forwarders[0].close_calls == 0


@pytest.mark.asyncio
async def test_container_playwright_recreates_container_between_retry_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)
    computer.connect_timeout_seconds = 5.0
    computer.connect_backoff_initial_seconds = 0.1
    computer.connect_backoff_max_seconds = 0.5

    list_calls = 0
    delete_calls: list[str] = []
    run_calls: list[dict[str, object]] = []

    async def _list():
        nonlocal list_calls
        list_calls += 1
        if list_calls == 1:
            return [
                SimpleNamespace(
                    id="container_1",
                    name="playwright",
                    state="RUNNING",
                )
            ]
        if list_calls in (2, 3):
            return [
                SimpleNamespace(
                    id="container_1",
                    name="playwright",
                    state="EXITED",
                )
            ]
        return [
            SimpleNamespace(
                id="container_2",
                name="playwright",
                state="RUNNING",
            )
        ]

    async def _delete(*, container_id: str) -> None:
        delete_calls.append(container_id)

    async def _run(**kwargs) -> str:
        run_calls.append(kwargs)
        return "container_2"

    room.containers = SimpleNamespace(list=_list, delete=_delete, run=_run)

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
        assert port == 3000
        assert token == "token"
        forwarder = _FakeForwarder(host="127.0.0.1", port=66000 + len(forwarders))
        forwarders.append(forwarder)
        return forwarder

    monkeypatch.setattr(container_playwright_module, "port_forward", _fake_port_forward)

    health_calls = 0

    async def _fake_health_check(*, base_url: str, timeout_seconds: float) -> str:
        del timeout_seconds
        nonlocal health_calls
        health_calls += 1
        if health_calls == 1:
            raise ConnectionError("playwright container is still starting")
        return base_url

    computer._check_server_ready_once = _fake_health_check  # type: ignore[method-assign]
    computer._check_websocket_ready_once = _fake_websocket_check_ready  # type: ignore[method-assign]

    restart_calls: list[bool] = []

    async def _restart_playwright_client() -> None:
        restart_calls.append(True)

    computer.restart_playwright_client = _restart_playwright_client  # type: ignore[method-assign]

    page = _FakePage()
    browser = _FakeBrowser(page=page)
    chromium = _FakeChromium(responses=[browser])
    computer._playwright = SimpleNamespace(chromium=chromium)

    connected_browser, connected_page = await computer._get_browser_and_page(
        _make_context()
    )

    assert connected_browser is browser
    assert connected_page is page
    assert delete_calls == ["container_1"]
    assert len(run_calls) == 1
    assert restart_calls == [True]
    assert sleep_calls == [0.1]
    assert len(forwarders) == 2
    assert forwarders[0].close_calls == 1
    assert forwarders[1].close_calls == 0
    assert chromium.connect_calls[0]["base_url"] == "ws://127.0.0.1:66001/"


@pytest.mark.asyncio
async def test_container_playwright_ensure_page_recreates_container_after_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    room = _FakeRoom()
    computer = ContainerPlaywrightComputer(room=room, headless=True)

    container_checks = 0

    async def _list():
        nonlocal container_checks
        container_checks += 1
        if container_checks == 1:
            return [
                SimpleNamespace(
                    id="container_1",
                    name="playwright",
                    state="EXITED",
                )
            ]
        return [
            SimpleNamespace(
                id="container_2",
                name="playwright",
                state="RUNNING",
            )
        ]

    delete_calls: list[str] = []

    async def _delete(*, container_id: str) -> None:
        delete_calls.append(container_id)

    async def _run(**kwargs) -> str:
        del kwargs
        return "container_2"

    room.containers = SimpleNamespace(list=_list, delete=_delete, run=_run)

    previous_browser = _FakeBrowser(page=_FakePage())
    previous_page = previous_browser._page
    assert previous_page is not None
    computer._browser = previous_browser
    computer._page = previous_page

    resolved_container = asyncio.Future[str]()
    resolved_container.set_result("container_1")
    computer.container_fut = resolved_container

    restart_calls: list[bool] = []

    async def _restart_playwright_client() -> None:
        restart_calls.append(True)
        computer._browser = None
        computer._page = None

    computer.restart_playwright_client = _restart_playwright_client  # type: ignore[method-assign]

    replacement_page = _FakePage()
    replacement_browser = _FakeBrowser(page=replacement_page)
    reconnect_calls: list[bool] = []

    async def _get_browser_and_page(
        context: ComputerContext,
    ) -> tuple[_FakeBrowser, _FakePage]:
        del context
        reconnect_calls.append(True)
        return replacement_browser, replacement_page

    computer._get_browser_and_page = _get_browser_and_page  # type: ignore[method-assign]

    await computer.ensure_page(_make_context())

    assert restart_calls == [True]
    assert reconnect_calls == [True]
    assert delete_calls == []
    assert await computer.container_fut == "container_2"
    assert computer._browser is replacement_browser
    assert computer._page is replacement_page
