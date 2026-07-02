import asyncio
from typing import Any

import pytest

from meshagent.computers import agent as agent_module
from meshagent.computers import base_playwright as base_playwright_module
from meshagent.computers.agent import ComputerToolkit
from meshagent.computers.base_playwright import BasePlaywrightComputer
from meshagent.computers.computer import ComputerContext
from meshagent.tools import ToolContext


class _FakeComputer:
    environment = "browser"
    dimensions = (1024, 768)

    def __init__(self) -> None:
        self.enter_contexts: list[ComputerContext] = []

    async def __aenter__(self, context: ComputerContext):
        self.enter_contexts.append(context)
        return self

    async def __aexit__(self, exc_type, exc, exc_tb):
        del exc_type
        del exc
        del exc_tb


class _FakeParticipant:
    def __init__(self, name: str):
        self._name = name

    def get_attribute(self, key: str) -> Any:
        if key == "name":
            return self._name
        return None


class _FakeRoom:
    def __init__(self, name: str):
        self.local_participant = _FakeParticipant(name=name)


class _FakeOperator:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    async def play(
        self,
        context: ComputerContext,
        *,
        computer: _FakeComputer,
        item: dict[str, Any],
    ) -> list[dict]:
        self.calls.append({"computer": computer, "item": item, "context": context})
        return [{"type": "computer_call_output", "output": None}]


def test_computer_toolkit_has_no_default_render_screen():
    toolkit = ComputerToolkit(
        computer=_FakeComputer(),
        room=_FakeRoom(name="agent"),
    )

    assert toolkit.render_screen is None


def test_computer_tool_does_not_share_default_rules_list():
    first = ComputerToolkit(
        computer=_FakeComputer(),
        operator=_FakeOperator(),
        room=_FakeRoom(name="agent"),
    )
    second = ComputerToolkit(
        computer=_FakeComputer(),
        operator=_FakeOperator(),
        room=_FakeRoom(name="agent"),
    )
    first_tool = next(tool for tool in first.tools if tool.name == "computer_call")
    second_tool = next(tool for tool in second.tools if tool.name == "computer_call")

    first_tool.rules.append("first only")

    assert first_tool.rules == ["first only"]
    assert second_tool.rules == []


def test_computer_tool_preserves_explicit_rules_list_reference():
    rules: list[str] = []
    toolkit = ComputerToolkit(
        computer=_FakeComputer(),
        operator=_FakeOperator(),
        room=_FakeRoom(name="agent"),
    )
    tool = type(next(tool for tool in toolkit.tools if tool.name == "computer_call"))(
        computer=_FakeComputer(),
        operator=_FakeOperator(),
        toolkit=toolkit,
        rules=rules,
    )

    rules.append("later")

    assert tool.rules is rules
    assert tool.rules == ["later"]


@pytest.mark.asyncio
async def test_computer_tool_emits_startup_progress_events():
    computer = _FakeComputer()
    operator = _FakeOperator()
    room = _FakeRoom(name="agent")
    toolkit = ComputerToolkit(
        computer=computer,
        operator=operator,
        room=room,
        render_screen=None,
    )
    events: list[dict[str, Any]] = []
    context = ToolContext(
        caller=room.local_participant,
        event_handler=events.append,
    )

    computer_tool = next(tool for tool in toolkit.tools if tool.name == "computer_call")
    result = await computer_tool.handle_computer_call(
        context=context,
        type="computer_call",
        action={"type": "wait"},
    )

    assert result == {"type": "computer_call_output", "output": None}
    assert len(events) == 2
    assert events[0]["type"] == "agent.event"
    assert events[0]["state"] == "in_progress"
    assert events[1]["type"] == "agent.event"
    assert events[1]["state"] == "completed"
    assert len(operator.calls) == 1
    assert operator.calls[0]["context"] is computer.enter_contexts[0]
    assert events[0]["headline"] == "Starting computer..."
    assert events[1]["headline"] == "Computer ready"
    assert events[0]["correlation_key"] == events[1]["correlation_key"]


@pytest.mark.asyncio
async def test_computer_tool_propagates_computer_startup_events_via_tool_context():
    class _StartupEmittingComputer(_FakeComputer):
        async def __aenter__(
            self,
            context: ComputerContext,
        ):
            self.enter_contexts.append(context)
            context.emit_startup(
                state="in_progress",
                details=("Waiting for Playwright container to become ready.",),
            )
            return self

    computer = _StartupEmittingComputer()
    operator = _FakeOperator()
    room = _FakeRoom(name="agent")
    toolkit = ComputerToolkit(
        computer=computer,
        operator=operator,
        room=room,
        render_screen=None,
    )
    events: list[dict[str, Any]] = []
    context = ToolContext(
        caller=room.local_participant,
        event_handler=events.append,
    )

    computer_tool = next(tool for tool in toolkit.tools if tool.name == "computer_call")
    await computer_tool.handle_computer_call(
        context=context,
        type="computer_call",
        action={"type": "wait"},
    )

    assert len(events) == 3
    assert events[0]["state"] == "in_progress"
    assert events[0]["details"] == []
    assert events[1]["state"] == "in_progress"
    assert events[1]["details"] == ["Waiting for Playwright container to become ready."]
    assert events[2]["state"] == "completed"


@pytest.mark.asyncio
async def test_computer_tool_render_screen_uses_python_lenient_base64_decode():
    class _OutputOperator:
        def __init__(self, image_url: str) -> None:
            self.image_url = image_url

        async def play(
            self,
            context: ComputerContext,
            *,
            computer: _FakeComputer,
            item: dict[str, Any],
        ) -> list[dict]:
            del context
            del computer
            del item
            return [
                {
                    "type": "computer_call_output",
                    "output": {
                        "type": "computer_screenshot",
                        "image_url": self.image_url,
                    },
                }
            ]

    rendered: list[bytes] = []
    room = _FakeRoom(name="agent")
    context = ToolContext(caller=room.local_participant)

    for image_url in (
        "data:image/png;base64,A Q I D",
        "data:image/png;base64,AQID====",
        "data:image/png;base64,@@@@",
        "data:image/png;base64,AQ-ID",
        "data:image/png;base64,AQ_ID",
        "data:image/png;base64,=AQID",
        "data:image/png;base64,AQ=ID",
    ):
        toolkit = ComputerToolkit(
            computer=_FakeComputer(),
            operator=_OutputOperator(image_url),
            room=room,
            render_screen=rendered.append,
        )
        computer_tool = next(
            tool for tool in toolkit.tools if tool.name == "computer_call"
        )

        await computer_tool.handle_computer_call(
            context=context,
            type="computer_call",
            action={"type": "wait"},
        )

    assert rendered == [
        b"\x01\x02\x03",
        b"\x01\x02\x03",
        b"",
        b"\x01\x02\x03",
        b"\x01\x02\x03",
        b"\x01\x02\x03",
        b"\x01\x02\x03",
    ]


@pytest.mark.asyncio
async def test_computer_tool_render_screen_rejects_non_ascii_base64_like_python():
    class _OutputOperator:
        async def play(
            self,
            context: ComputerContext,
            *,
            computer: _FakeComputer,
            item: dict[str, Any],
        ) -> list[dict]:
            del context
            del computer
            del item
            return [
                {
                    "type": "computer_call_output",
                    "output": {
                        "type": "computer_screenshot",
                        "image_url": "data:image/png;base64,AQéID",
                    },
                }
            ]

    room = _FakeRoom(name="agent")
    toolkit = ComputerToolkit(
        computer=_FakeComputer(),
        operator=_OutputOperator(),
        room=room,
        render_screen=lambda _: None,
    )
    computer_tool = next(tool for tool in toolkit.tools if tool.name == "computer_call")

    with pytest.raises(ValueError, match="string argument should contain only ASCII"):
        await computer_tool.handle_computer_call(
            context=ToolContext(caller=room.local_participant),
            type="computer_call",
            action={"type": "wait"},
        )


@pytest.mark.asyncio
async def test_restart_playwright_client_does_not_block_on_stuck_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _TestComputer(BasePlaywrightComputer):
        async def _get_browser_and_page(
            self,
            context: ComputerContext,
        ):
            del context
            raise AssertionError("not used in this test")

    class _StuckBrowser:
        def __init__(self) -> None:
            self.cancelled = asyncio.Event()
            self.release = asyncio.Event()
            self.task: asyncio.Task[object] | None = None

        async def close(self) -> None:
            self.task = asyncio.current_task()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.cancelled.set()
                await self.release.wait()
                raise

    class _StuckContextManager:
        def __init__(self) -> None:
            self.cancelled = asyncio.Event()
            self.release = asyncio.Event()
            self.task: asyncio.Task[object] | None = None

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, exc_type, exc, exc_tb) -> None:
            del exc_type
            del exc
            del exc_tb
            self.task = asyncio.current_task()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.cancelled.set()
                await self.release.wait()
                raise

    class _ReplacementContextManager:
        def __init__(self) -> None:
            self.enter_calls = 0

        async def __aenter__(self) -> object:
            self.enter_calls += 1
            return object()

        async def __aexit__(self, exc_type, exc, exc_tb) -> None:
            del exc_type
            del exc
            del exc_tb

    replacement_context = _ReplacementContextManager()
    monkeypatch.setattr(
        base_playwright_module,
        "_PLAYWRIGHT_CONTEXT_RESTART_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        base_playwright_module,
        "async_playwright",
        lambda: replacement_context,
    )

    computer = _TestComputer()
    old_browser = _StuckBrowser()
    old_context = _StuckContextManager()
    computer._browser = old_browser  # type: ignore[assignment]
    computer._context = old_context  # type: ignore[assignment]
    computer._playwright = object()

    await computer.restart_playwright_client()

    assert old_browser.cancelled.is_set()
    assert old_context.cancelled.is_set()
    assert replacement_context.enter_calls == 1
    assert computer._browser is None
    assert computer._page is None
    assert computer._context is replacement_context
    assert computer._playwright is not None

    old_browser.release.set()
    old_context.release.set()
    if old_browser.task is not None:
        await asyncio.wait_for(
            asyncio.gather(old_browser.task, return_exceptions=True),
            timeout=1.0,
        )
    if old_context.task is not None:
        await asyncio.wait_for(
            asyncio.gather(old_context.task, return_exceptions=True),
            timeout=1.0,
        )


def test_computer_tool_uses_responses_computer_type():
    toolkit = ComputerToolkit(
        computer=_FakeComputer(),
        operator=_FakeOperator(),
        room=_FakeRoom(name="agent"),
        render_screen=None,
    )
    computer_tool = next(tool for tool in toolkit.tools if tool.name == "computer_call")
    definitions = computer_tool.get_open_ai_tool_definitions()
    assert definitions == [{"type": "computer"}]


def test_computer_tool_handles_computer_call_output_items():
    toolkit = ComputerToolkit(
        computer=_FakeComputer(),
        operator=_FakeOperator(),
        room=_FakeRoom(name="agent"),
        render_screen=None,
    )
    computer_tool = next(tool for tool in toolkit.tools if tool.name == "computer_call")

    handlers = computer_tool.get_open_ai_output_handlers()

    assert handlers["computer_call"] == computer_tool.handle_computer_call


def test_computer_toolkit_only_exposes_native_computer_tool_by_default():
    toolkit = ComputerToolkit(
        computer=_FakeComputer(),
        operator=_FakeOperator(),
        room=_FakeRoom(name="agent"),
        render_screen=None,
    )

    assert [tool.name for tool in toolkit.tools] == ["computer_call"]


def test_computer_toolkit_rejects_goto_for_non_playwright_computers():
    with pytest.raises(ValueError, match="goto tool requires a Playwright computer"):
        ComputerToolkit(
            computer=_FakeComputer(),
            operator=_FakeOperator(),
            room=_FakeRoom(name="agent"),
            render_screen=None,
            include_goto_tool=True,
        )


def test_computer_toolkit_can_include_goto_for_playwright_computers():
    class _FakePlaywrightComputer(BasePlaywrightComputer):
        async def _get_browser_and_page(
            self,
            context: ComputerContext,
        ):
            del context
            raise AssertionError("test should not start Playwright")

    toolkit = ComputerToolkit(
        computer=_FakePlaywrightComputer(),
        operator=_FakeOperator(),
        room=_FakeRoom(name="agent"),
        render_screen=None,
        include_goto_tool=True,
    )

    assert [tool.name for tool in toolkit.tools] == ["computer_call", "goto"]


def test_computer_toolkit_passes_dimensions_to_container_computer(
    monkeypatch: pytest.MonkeyPatch,
):
    recorded: dict[str, Any] = {}
    monkeypatch.setattr(agent_module, "stagehand_available", lambda: False)

    class _FakeContainerPlaywrightComputer:
        environment = "browser"

        def __init__(
            self,
            *,
            room: _FakeRoom,
            headless: bool,
            dimensions: tuple[int, int] | None = None,
            starting_url: str | None = None,
        ):
            recorded["room"] = room
            recorded["headless"] = headless
            recorded["dimensions"] = dimensions
            recorded["starting_url"] = starting_url
            self.dimensions = dimensions or (1440, 900)
            self.starting_url = starting_url or "https://google.com"

    monkeypatch.setattr(
        agent_module,
        "ContainerPlaywrightComputer",
        _FakeContainerPlaywrightComputer,
    )

    room = _FakeRoom(name="agent")
    toolkit = ComputerToolkit(
        room=room,
        dimensions=(1600, 900),
        render_screen=None,
    )

    assert recorded["room"] is room
    assert recorded["headless"] is True
    assert recorded["dimensions"] == (1600, 900)
    assert recorded["starting_url"] is None
    assert toolkit.computer.dimensions == (1600, 900)


def test_computer_toolkit_passes_dimensions_to_local_computer(
    monkeypatch: pytest.MonkeyPatch,
):
    recorded: dict[str, Any] = {}
    monkeypatch.setattr(agent_module, "stagehand_available", lambda: False)

    class _FakeLocalPlaywrightComputer:
        environment = "browser"

        def __init__(
            self,
            *,
            dimensions: tuple[int, int] | None = None,
            starting_url: str | None = None,
        ):
            recorded["dimensions"] = dimensions
            recorded["starting_url"] = starting_url
            self.dimensions = dimensions or (1440, 900)
            self.starting_url = starting_url or "https://google.com"

    monkeypatch.setattr(
        agent_module,
        "LocalPlaywrightComputer",
        _FakeLocalPlaywrightComputer,
    )

    toolkit = ComputerToolkit(
        dimensions=(1600, 900),
        render_screen=None,
    )

    assert recorded["dimensions"] == (1600, 900)
    assert recorded["starting_url"] is None
    assert toolkit.computer.dimensions == (1600, 900)


def test_computer_toolkit_passes_starting_url_to_container_computer(
    monkeypatch: pytest.MonkeyPatch,
):
    recorded: dict[str, Any] = {}
    monkeypatch.setattr(agent_module, "stagehand_available", lambda: False)

    class _FakeContainerPlaywrightComputer:
        environment = "browser"

        def __init__(
            self,
            *,
            room: _FakeRoom,
            headless: bool,
            dimensions: tuple[int, int] | None = None,
            starting_url: str | None = None,
        ):
            recorded["room"] = room
            recorded["headless"] = headless
            recorded["dimensions"] = dimensions
            recorded["starting_url"] = starting_url
            self.dimensions = dimensions or (1440, 900)
            self.starting_url = starting_url or "https://google.com"

    monkeypatch.setattr(
        agent_module,
        "ContainerPlaywrightComputer",
        _FakeContainerPlaywrightComputer,
    )

    room = _FakeRoom(name="agent")
    toolkit = ComputerToolkit(
        room=room,
        starting_url="https://example.com",
        render_screen=None,
    )

    assert recorded["room"] is room
    assert recorded["headless"] is True
    assert recorded["dimensions"] is None
    assert recorded["starting_url"] == "https://example.com"
    assert toolkit.computer.starting_url == "https://example.com"


def test_computer_toolkit_passes_starting_url_to_local_computer(
    monkeypatch: pytest.MonkeyPatch,
):
    recorded: dict[str, Any] = {}
    monkeypatch.setattr(agent_module, "stagehand_available", lambda: False)

    class _FakeLocalPlaywrightComputer:
        environment = "browser"

        def __init__(
            self,
            *,
            dimensions: tuple[int, int] | None = None,
            starting_url: str | None = None,
        ):
            recorded["dimensions"] = dimensions
            recorded["starting_url"] = starting_url
            self.dimensions = dimensions or (1440, 900)
            self.starting_url = starting_url or "https://google.com"

    monkeypatch.setattr(
        agent_module,
        "LocalPlaywrightComputer",
        _FakeLocalPlaywrightComputer,
    )

    toolkit = ComputerToolkit(
        starting_url="https://example.com",
        render_screen=None,
    )

    assert recorded["dimensions"] is None
    assert recorded["starting_url"] == "https://example.com"
    assert toolkit.computer.starting_url == "https://example.com"


def test_computer_toolkit_rejects_starting_url_for_non_playwright_computers():
    with pytest.raises(ValueError, match="starting_url requires a Playwright computer"):
        ComputerToolkit(
            computer=_FakeComputer(),
            room=_FakeRoom(name="agent"),
            starting_url="https://example.com",
        )


def test_computer_toolkit_does_not_override_provided_computer_with_blank_starting_url():
    class _TestComputer(BasePlaywrightComputer):
        async def _get_browser_and_page(
            self,
            context: ComputerContext,
        ):
            del context
            raise AssertionError("not used in this test")

    computer = _TestComputer(starting_url="https://keep.example.test")

    ComputerToolkit(
        computer=computer,
        starting_url="   ",
        render_screen=None,
    )

    assert computer.starting_url == "https://keep.example.test"


def test_computer_toolkit_rejects_unsupported_dimensions():
    with pytest.raises(ValueError, match="dimensions must be one of"):
        ComputerToolkit(dimensions=(1024, 768))


def test_computer_toolkit_prefers_stagehand_when_available(
    monkeypatch: pytest.MonkeyPatch,
):
    recorded: dict[str, Any] = {}

    class _FakeStagehandComputer:
        environment = "browser"

        def __init__(
            self,
            *,
            dimensions: tuple[int, int] | None = None,
            starting_url: str | None = None,
        ) -> None:
            recorded["dimensions"] = dimensions
            recorded["starting_url"] = starting_url
            self.dimensions = dimensions or (1440, 900)
            self.starting_url = starting_url or "https://google.com"

    monkeypatch.setattr(agent_module, "stagehand_available", lambda: True)
    monkeypatch.setattr(agent_module, "StagehandComputer", _FakeStagehandComputer)

    toolkit = ComputerToolkit(
        room=_FakeRoom(name="agent"),
        dimensions=(1600, 900),
        starting_url="https://example.com",
        render_screen=None,
    )

    assert recorded["dimensions"] == (1600, 900)
    assert recorded["starting_url"] == "https://example.com"
    assert toolkit.computer.starting_url == "https://example.com"
    assert toolkit.computer.dimensions == (1600, 900)
