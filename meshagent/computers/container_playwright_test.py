from __future__ import annotations

from types import SimpleNamespace

import pytest

from meshagent.computers import container_playwright as container_playwright_module
from meshagent.computers.container_playwright import ContainerPlaywrightComputer


def test_playwright_version_falls_back_to_repo_module_and_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _missing_metadata(name: str) -> str:
        del name
        raise container_playwright_module.PackageNotFoundError("playwright")

    monkeypatch.setattr(
        container_playwright_module,
        "package_version",
        _missing_metadata,
    )
    monkeypatch.setattr(
        container_playwright_module,
        "import_module",
        lambda module: SimpleNamespace(version="1.58.0"),
    )

    caplog.set_level("WARNING", logger="computer_use")

    assert container_playwright_module._playwright_version() == "1.58.0"
    assert any(
        "package metadata is unavailable" in record.message for record in caplog.records
    )


def test_playwright_version_no_warning_when_fallback_module_is_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _missing_metadata(name: str) -> str:
        del name
        raise container_playwright_module.PackageNotFoundError("playwright")

    def _missing_module(module: str) -> SimpleNamespace:
        del module
        raise ModuleNotFoundError("playwright._repo_version")

    monkeypatch.setattr(
        container_playwright_module,
        "package_version",
        _missing_metadata,
    )
    monkeypatch.setattr(
        container_playwright_module,
        "import_module",
        _missing_module,
    )

    caplog.set_level("WARNING", logger="computer_use")

    with pytest.raises(RuntimeError, match="playwright is not installed"):
        container_playwright_module._playwright_version()

    assert not any(
        "package metadata is unavailable" in record.message for record in caplog.records
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
    def __init__(self, *, page: _FakePage) -> None:
        self._page = page

    async def new_page(self) -> _FakePage:
        return self._page


class _FakeChromium:
    def __init__(self, *, responses: list[object]) -> None:
        self._responses = list(responses)
        self.connect_calls: list[dict[str, object]] = []

    async def connect(self, base_url: str, headers: dict[str, str]) -> object:
        self.connect_calls.append(
            {
                "base_url": base_url,
                "headers": headers,
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

    page = _FakePage()
    browser = _FakeBrowser(page=page)
    chromium = _FakeChromium(
        responses=[
            RuntimeError("dial tcp4 127.0.0.1:3000: connect: connection refused"),
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
    assert sleep_calls == [0.1]
    assert len(forwarders) == 2
    assert forwarders[0].close_calls == 1
    assert forwarders[1].close_calls == 0
    assert page.viewport_calls == [{"width": 1024, "height": 768}]
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

    chromium = _FakeChromium(
        responses=[
            RuntimeError("dial tcp4 127.0.0.1:3000: connect: connection refused"),
        ]
    )
    computer._playwright = SimpleNamespace(chromium=chromium)

    with pytest.raises(
        TimeoutError, match="timed out waiting for playwright port forward"
    ):
        await computer._get_browser_and_page()

    assert len(chromium.connect_calls) == 1
    assert len(forwarders) == 1
    assert forwarders[0].close_calls == 1
