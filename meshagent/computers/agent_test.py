import asyncio
import binascii
import re
from typing import Any

import pytest

from meshagent.computers import agent as agent_module
from meshagent.computers import base_playwright as base_playwright_module
from meshagent.computers.agent import ComputerTool, ComputerToolkit
from meshagent.computers.base_playwright import BasePlaywrightComputer
from meshagent.computers.computer import Computer, ComputerContext
from meshagent.tools import RoomToolContext, ToolContext


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


class _FakeStorage:
    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []

    async def upload(self, **kwargs: Any) -> None:
        self.uploads.append(kwargs)


class _FakeRoomWithStorage(_FakeRoom):
    def __init__(self, name: str):
        super().__init__(name=name)
        self.storage = _FakeStorage()


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


def test_computer_tool_metadata_defaults_and_overrides_match_python():
    toolkit = ComputerToolkit(
        computer=_FakeComputer(),
        operator=_FakeOperator(),
        room=_FakeRoom(name="agent"),
    )
    default_tool = next(tool for tool in toolkit.tools if tool.name == "computer_call")

    assert default_tool.name == "computer_call"
    assert default_tool.title == "computer_call"
    assert default_tool.description == "handle computer tool calls"
    assert default_tool.rules == []

    custom_rules = ["rule one"]
    custom_tool = ComputerTool(
        computer=_FakeComputer(),
        operator=_FakeOperator(),
        toolkit=toolkit,
        title="custom title",
        description="custom description",
        rules=custom_rules,
    )

    assert custom_tool.name == "computer_call"
    assert custom_tool.title == "custom title"
    assert custom_tool.description == "custom description"
    assert custom_tool.rules is custom_rules
    assert custom_tool.rules == ["rule one"]


def test_computer_context_emit_invokes_event_handler_like_python():
    events: list[dict[str, Any]] = []
    room = _FakeRoom(name="agent")
    context = ComputerContext(
        room=room,
        caller=room.local_participant,
        event_handler=events.append,
    )

    event = {"type": "custom", "nested": {"value": 1}}
    context.emit(event)
    ComputerContext(room=room, caller=room.local_participant).emit({"ignored": True})

    assert events == [event]


def test_computer_context_startup_events_match_python_dedupe_and_details():
    events: list[dict[str, Any]] = []
    room = _FakeRoom(name="agent")

    def make_startup_event(state: str, details: tuple[str, ...]) -> dict[str, Any]:
        return {"type": "startup", "state": state, "details": list(details)}

    context = ComputerContext(
        room=room,
        caller=room.local_participant,
        event_handler=events.append,
        startup_event_factory=make_startup_event,
    )

    assert context.room is room
    assert context.caller is room.local_participant
    assert context.on_behalf_of is None
    assert context.last_startup_state is None
    assert context.last_startup_details == ()

    context.emit_startup(state="queued", details=[" one ", "", 7, "two"])  # type: ignore[list-item]
    context.emit_startup(state="queued", details=("one", "two"))
    context.emit_startup(state="in_progress")

    assert events == [
        {"type": "startup", "state": "queued", "details": ["one", "two"]},
        {"type": "startup", "state": "in_progress", "details": []},
    ]
    assert context.last_startup_state == "in_progress"
    assert context.last_startup_details == ()

    no_factory = ComputerContext(room=room, caller=room.local_participant)
    no_factory.emit_startup(state="completed", details=["ignored"])
    assert no_factory.last_startup_state is None
    assert no_factory.last_startup_details == ()


@pytest.mark.asyncio
async def test_computer_protocol_default_context_manager_returns_self() -> None:
    class _DefaultContextManagerComputer(Computer):
        environment = "browser"
        dimensions = (1, 1)

    computer = _DefaultContextManagerComputer()

    assert await computer.__aenter__(object()) is computer  # type: ignore[arg-type]
    assert (
        await computer.__aexit__(Exception, Exception("boom"), object())  # type: ignore[arg-type]
        is computer
    )


def test_base_playwright_constructor_helpers_match_python_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert base_playwright_module._parse_dimensions("") is None  # noqa: SLF001
    assert base_playwright_module._parse_dimensions(" 1440x900 ") == (  # noqa: SLF001
        1440,
        900,
    )
    assert base_playwright_module._parse_dimensions("1440X900") == (  # noqa: SLF001
        1440,
        900,
    )
    assert base_playwright_module._parse_dimensions("1600, 900") == (  # noqa: SLF001
        1600,
        900,
    )
    assert base_playwright_module._parse_dimensions("1600:900") is None  # noqa: SLF001
    assert base_playwright_module._parse_dimensions("1x2x3") is None  # noqa: SLF001
    assert base_playwright_module._parse_dimensions("1,2,3") is None  # noqa: SLF001
    assert base_playwright_module._parse_dimensions("wide x 900") is None  # noqa: SLF001

    monkeypatch.delenv("MESHAGENT_PLAYWRIGHT_DIMENSIONS", raising=False)
    assert base_playwright_module._resolve_playwright_dimensions() == (  # noqa: SLF001
        1440,
        900,
    )
    monkeypatch.setenv("MESHAGENT_PLAYWRIGHT_DIMENSIONS", "1600x900")
    assert base_playwright_module._resolve_playwright_dimensions() == (  # noqa: SLF001
        1600,
        900,
    )
    monkeypatch.setenv("MESHAGENT_PLAYWRIGHT_DIMENSIONS", "1200x800")
    assert base_playwright_module._resolve_playwright_dimensions() == (  # noqa: SLF001
        1440,
        900,
    )

    default_computer = BasePlaywrightComputer(starting_url=" ")
    assert default_computer.dimensions == (1440, 900)
    assert default_computer.starting_url == "https://google.com"
    custom_computer = BasePlaywrightComputer(
        dimensions=(1600, 900),
        starting_url="  https://example.test  ",
    )
    assert custom_computer.dimensions == (1600, 900)
    assert custom_computer.starting_url == "  https://example.test  "
    with pytest.raises(
        ValueError,
        match=r"playwright dimensions must be one of: \(1440, 900\), \(1600, 900\)",
    ):
        BasePlaywrightComputer(dimensions=(1200, 800))

    for input_key, expected in [
        ("/", "Divide"),
        ("\\", "Backslash"),
        ("alt", "Alt"),
        ("arrowdown", "ArrowDown"),
        ("arrowleft", "ArrowLeft"),
        ("arrowright", "ArrowRight"),
        ("arrowup", "ArrowUp"),
        ("backspace", "Backspace"),
        ("capslock", "CapsLock"),
        ("cmd", "Meta"),
        ("ctrl", "Control"),
        ("delete", "Delete"),
        ("end", "End"),
        ("enter", "Enter"),
        ("esc", "Escape"),
        ("home", "Home"),
        ("insert", "Insert"),
        ("option", "Alt"),
        ("pagedown", "PageDown"),
        ("pageup", "PageUp"),
        ("shift", "Shift"),
        ("space", " "),
        ("super", "Meta"),
        ("tab", "Tab"),
        ("win", "Meta"),
        ("CTRL", "Control"),
        ("A", "A"),
    ]:
        assert (
            base_playwright_module.CUA_KEY_TO_PLAYWRIGHT_KEY.get(
                input_key.lower(),
                input_key,
            )
            == expected
        )


@pytest.mark.asyncio
async def test_base_playwright_common_actions_match_python_page_calls() -> None:
    calls: list[tuple[Any, ...]] = []

    class _Mouse:
        async def click(self, x: int, y: int, *, button: str) -> None:
            calls.append(("mouse.click", x, y, button))

        async def wheel(self, x: int, y: int) -> None:
            calls.append(("mouse.wheel", x, y))

        async def dblclick(self, x: int, y: int) -> None:
            calls.append(("mouse.dblclick", x, y))

        async def move(self, x: int, y: int) -> None:
            calls.append(("mouse.move", x, y))

        async def down(self) -> None:
            calls.append(("mouse.down",))

        async def up(self) -> None:
            calls.append(("mouse.up",))

    class _Keyboard:
        async def type(self, text: str) -> None:
            calls.append(("keyboard.type", text))

        async def press(self, key: str) -> None:
            calls.append(("keyboard.press", key))

    class _Page:
        def __init__(self) -> None:
            self.mouse = _Mouse()
            self.keyboard = _Keyboard()
            self.url = "https://google.com"

        async def screenshot(self, *, full_page: bool) -> bytes:
            calls.append(("page.screenshot", full_page))
            return b"png"

        async def evaluate(self, script: str) -> None:
            calls.append(("page.evaluate", script))

        async def goto(self, url: str) -> None:
            calls.append(("page.goto", url))
            self.url = url

        async def go_back(self) -> None:
            calls.append(("page.go_back",))

        async def go_forward(self) -> None:
            calls.append(("page.go_forward",))

    class _Browser:
        def is_connected(self) -> bool:
            return True

    class _TestComputer(BasePlaywrightComputer):
        async def ensure_page(self, context: ComputerContext):
            calls.append(("ensure_page",))
            await super().ensure_page(context)

        async def _get_browser_and_page(
            self,
            context: ComputerContext,
        ):
            del context
            raise AssertionError("not used in this test")

    context = object()
    computer = _TestComputer(dimensions=(1600, 900))
    computer._browser = _Browser()  # type: ignore[assignment]
    computer._page = _Page()  # type: ignore[assignment]

    await computer.click(context=context, x=10, y=20, button="middle")
    await computer.click(context=context, x=11, y=21, button="right")
    await computer.click(context=context, x=12, y=22, button="wheel")
    await computer.click(context=context, x=0, y=0, button="back")
    await computer.double_click(context=context, x=13, y=23)
    await computer.scroll(context=context, x=1, y=2, scroll_x=3, scroll_y=4)
    await computer.type(context=context, text="hello")
    await computer.move(context=context, x=4, y=5)
    await computer.keypress(context=context, keys=["ctrl", "A", "/"])
    await computer.wait(context=context, ms=0)
    with pytest.raises(ValueError, match="sleep length must be non-negative"):
        await computer.wait(context=context, ms=-1)
    await computer.drag(context=context, path=[])
    await computer.drag(context=context, path=[{"x": 5, "y": 6}, {"x": 7, "y": 8}])
    assert await computer.get_current_url(context=context) == "https://google.com"
    await computer.goto(context=context, url="https://example.test/next")
    assert (
        await computer.get_current_url(context=context) == "https://example.test/next"
    )
    await computer.back(context=context)
    await computer.forward(context=context)
    assert await computer.screenshot(context=context) == "cG5n"
    assert await computer.screenshot_bytes(context=context, full_page=True) == b"png"

    assert calls == [
        ("ensure_page",),
        ("mouse.click", 10, 20, "left"),
        ("ensure_page",),
        ("mouse.click", 11, 21, "right"),
        ("ensure_page",),
        ("mouse.wheel", 12, 22),
        ("ensure_page",),
        ("ensure_page",),
        ("page.go_back",),
        ("ensure_page",),
        ("mouse.dblclick", 13, 23),
        ("ensure_page",),
        ("mouse.move", 1, 2),
        ("page.evaluate", "window.scrollBy(3, 4)"),
        ("ensure_page",),
        ("keyboard.type", "hello"),
        ("ensure_page",),
        ("mouse.move", 4, 5),
        ("ensure_page",),
        ("keyboard.press", "Control"),
        ("keyboard.press", "A"),
        ("keyboard.press", "Divide"),
        ("ensure_page",),
        ("ensure_page",),
        ("ensure_page",),
        ("ensure_page",),
        ("mouse.move", 5, 6),
        ("mouse.down",),
        ("mouse.move", 7, 8),
        ("mouse.up",),
        ("ensure_page",),
        ("ensure_page",),
        ("page.goto", "https://example.test/next"),
        ("ensure_page",),
        ("ensure_page",),
        ("page.go_back",),
        ("ensure_page",),
        ("page.go_forward",),
        ("ensure_page",),
        ("ensure_page",),
        ("page.screenshot", False),
        ("ensure_page",),
        ("page.screenshot", True),
    ]


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
async def test_computer_tool_startup_failure_event_matches_python_suppression():
    class _FailingComputer(_FakeComputer):
        def __init__(self, emitted_state: str | None) -> None:
            super().__init__()
            self.emitted_state = emitted_state

        async def __aenter__(
            self,
            context: ComputerContext,
        ):
            self.enter_contexts.append(context)
            if self.emitted_state is not None:
                context.emit_startup(state=self.emitted_state)
            raise RuntimeError("boom")

    for emitted_state, expected_states in (
        (None, ["in_progress", "failed"]),
        ("failed", ["in_progress", "failed"]),
        ("cancelled", ["in_progress", "cancelled"]),
    ):
        computer = _FailingComputer(emitted_state=emitted_state)
        room = _FakeRoom(name="agent")
        toolkit = ComputerToolkit(
            computer=computer,
            operator=_FakeOperator(),
            room=room,
            render_screen=None,
        )
        events: list[dict[str, Any]] = []
        context = ToolContext(
            caller=room.local_participant,
            event_handler=events.append,
        )

        computer_tool = next(
            tool for tool in toolkit.tools if tool.name == "computer_call"
        )
        with pytest.raises(RuntimeError, match="boom"):
            await computer_tool.handle_computer_call(
                context=context,
                type="computer_call",
                action={"type": "wait"},
            )

        assert [event["state"] for event in events] == expected_states


@pytest.mark.asyncio
async def test_screenshot_tool_execute_matches_python_side_effects():
    class _ScreenshotComputer(_FakeComputer):
        def __init__(self) -> None:
            super().__init__()
            self.screenshot_calls: list[dict[str, Any]] = []

        async def screenshot_bytes(
            self,
            context: ComputerContext,
            *,
            full_page: bool,
        ) -> bytes:
            self.screenshot_calls.append(
                {"context": context, "full_page": full_page},
            )
            return b"png-bytes"

    computer = _ScreenshotComputer()
    room = _FakeRoomWithStorage(name="agent")
    toolkit = ComputerToolkit(
        computer=computer,
        operator=_FakeOperator(),
        room=room,
        render_screen=None,
    )
    events: list[dict[str, Any]] = []
    context = ToolContext(caller=room.local_participant, event_handler=events.append)
    tool = agent_module.ScreenshotTool(
        room=room,
        computer=computer,
        toolkit=toolkit,
    )

    assert tool.name == "screenshot"
    assert tool.description == "take a screenshot of the current page"
    assert tool.input_schema == {
        "additionalProperties": False,
        "type": "object",
        "required": ["full_page", "save_path"],
        "properties": {
            "full_page": {"type": "boolean"},
            "save_path": {
                "type": "string",
                "description": "a file path to save the screenshot to (should end with .png)",
            },
        },
    }

    result = await tool.execute(context=context, save_path="screen.png", full_page=True)

    assert result == "saved screenshot to screen.png"
    assert len(computer.enter_contexts) == 1
    assert computer.screenshot_calls == [
        {"context": computer.enter_contexts[0], "full_page": True},
    ]
    assert room.storage.uploads == [
        {"path": "screen.png", "data": b"png-bytes", "overwrite": True},
    ]
    assert [event["state"] for event in events] == ["in_progress", "completed"]


@pytest.mark.asyncio
async def test_goto_tool_execute_matches_python_navigation_and_rendering():
    class _GotoComputer(_FakeComputer):
        def __init__(self) -> None:
            super().__init__()
            self.goto_calls: list[dict[str, Any]] = []
            self.screenshot_calls: list[dict[str, Any]] = []

        async def goto(
            self,
            context: ComputerContext,
            *,
            url: str,
        ) -> None:
            self.goto_calls.append({"context": context, "url": url})

        async def screenshot_bytes(
            self,
            context: ComputerContext,
            *,
            full_page: bool,
        ) -> bytes:
            self.screenshot_calls.append(
                {"context": context, "full_page": full_page},
            )
            return b"rendered-png"

    computer = _GotoComputer()
    room = _FakeRoom(name="agent")
    rendered: list[bytes] = []
    render_called = asyncio.Event()

    async def render_screen(data: bytes) -> None:
        rendered.append(data)
        render_called.set()

    toolkit = ComputerToolkit(
        computer=computer,
        operator=_FakeOperator(),
        room=room,
        render_screen=render_screen,
    )
    events: list[dict[str, Any]] = []
    context = ToolContext(caller=room.local_participant, event_handler=events.append)
    tool = agent_module.GotoURL(
        computer=computer,
        toolkit=toolkit,
        render_screen=render_screen,
    )

    assert tool.name == "goto"
    assert (
        tool.description
        == "goes to a specific URL. Make sure it starts with http:// or https://"
    )
    assert tool.input_schema == {
        "additionalProperties": False,
        "type": "object",
        "required": ["url"],
        "properties": {
            "url": {
                "type": "string",
                "description": "Fully qualified URL to navigate to.",
            }
        },
    }

    result = await tool.execute(context=context, url="example.test/path")

    assert result is None
    assert render_called.is_set()
    assert rendered == [b"rendered-png"]
    assert len(computer.enter_contexts) == 1
    assert computer.goto_calls == [
        {
            "context": computer.enter_contexts[0],
            "url": "https://example.test/path",
        },
    ]
    assert computer.screenshot_calls == [
        {"context": computer.enter_contexts[0], "full_page": False},
    ]
    assert [event["state"] for event in events] == ["in_progress", "completed"]

    await tool.execute(context=context, url="http://example.test/next")
    assert computer.goto_calls[-1]["url"] == "http://example.test/next"


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
@pytest.mark.parametrize(
    ("output", "expected_type", "expected_message"),
    [
        ([], TypeError, "list indices must be integers or slices, not str"),
        ({"output": None}, KeyError, "'type'"),
        ({"type": "computer_call_output"}, KeyError, "'output'"),
        (
            {"type": "computer_call_output", "output": []},
            AttributeError,
            "'list' object has no attribute 'get'",
        ),
        (
            {"type": "computer_call_output", "output": {"type": "computer_screenshot"}},
            KeyError,
            "'image_url'",
        ),
        (
            {
                "type": "computer_call_output",
                "output": {"type": "computer_screenshot", "image_url": 1},
            },
            AttributeError,
            "'int' object has no attribute 'split'",
        ),
        (
            {
                "type": "computer_call_output",
                "output": {
                    "type": "computer_screenshot",
                    "image_url": "not-a-data-url",
                },
            },
            IndexError,
            "list index out of range",
        ),
        (
            {
                "type": "computer_call_output",
                "output": {
                    "type": "computer_screenshot",
                    "image_url": "data:image/png;base64,AA=A",
                },
            },
            binascii.Error,
            "Incorrect padding",
        ),
        (
            {
                "type": "computer_call_output",
                "output": {
                    "type": "computer_screenshot",
                    "image_url": "data:image/png;base64,AQI",
                },
            },
            binascii.Error,
            "Incorrect padding",
        ),
        (
            {
                "type": "computer_call_output",
                "output": {
                    "type": "computer_screenshot",
                    "image_url": "data:image/png;base64,A",
                },
            },
            binascii.Error,
            "Invalid base64-encoded string: number of data characters (1) cannot be 1 more than a multiple of 4",
        ),
    ],
)
async def test_computer_tool_render_screen_malformed_outputs_error_like_python(
    output: Any,
    expected_type: type[BaseException],
    expected_message: str,
) -> None:
    class _OutputOperator:
        async def play(
            self,
            context: ComputerContext,
            *,
            computer: _FakeComputer,
            item: dict[str, Any],
        ) -> list[Any]:
            del context
            del computer
            del item
            return [output]

    room = _FakeRoom(name="agent")
    toolkit = ComputerToolkit(
        computer=_FakeComputer(),
        operator=_OutputOperator(),
        room=room,
        render_screen=lambda _: None,
    )
    computer_tool = next(tool for tool in toolkit.tools if tool.name == "computer_call")

    with pytest.raises(expected_type, match=re.escape(expected_message)):
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


@pytest.mark.asyncio
async def test_base_playwright_route_callback_aborts_blocked_domains_like_python_source(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registered_route: dict[str, Any] = {}

    class _ContextManager:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, exc_type, exc, exc_tb) -> None:
            del exc_type
            del exc
            del exc_tb

    class _Page:
        async def route(self, pattern: str, callback) -> None:
            registered_route["pattern"] = pattern
            registered_route["callback"] = callback

    class _Browser:
        pass

    class _Request:
        def __init__(self, url: str) -> None:
            self.url = url

    class _Route:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def abort(self) -> None:
            self.calls.append("abort")

        async def continue_(self) -> None:
            self.calls.append("continue")

    class _TestComputer(BasePlaywrightComputer):
        async def _get_browser_and_page(
            self,
            context: ComputerContext,
        ):
            del context
            return _Browser(), _Page()

    monkeypatch.setattr(
        base_playwright_module,
        "async_playwright",
        lambda: _ContextManager(),
    )

    computer = _TestComputer()
    await computer.__aenter__(context=object())

    assert registered_route["pattern"] == "**/*"
    callback = registered_route["callback"]

    safe_route = _Route()
    await callback(safe_route, _Request("https://safe.example"))
    assert safe_route.calls == ["continue"]

    blocked_route = _Route()
    await callback(blocked_route, _Request("https://evilvideos.com/watch"))
    assert blocked_route.calls == ["abort"]
    assert (
        "Flagging blocked domain: https://evilvideos.com/watch"
        in capsys.readouterr().out
    )

    malformed_route = _Route()
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        await callback(malformed_route, _Request("http://[::1"))
    assert malformed_route.calls == []


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


def test_computer_toolkit_context_uses_room_tool_context_like_python() -> None:
    fallback_room = _FakeRoom(name="fallback")
    tool_room = _FakeRoom(name="tool-room")
    events: list[dict[str, Any]] = []
    caller = _FakeParticipant(name="caller")
    on_behalf_of = _FakeParticipant(name="sender")
    toolkit = ComputerToolkit(
        name="context.computer",
        computer=_FakeComputer(),
        operator=_FakeOperator(),
        room=fallback_room,
        render_screen=None,
    )
    tool_context = RoomToolContext(
        room=tool_room,
        caller=caller,
        on_behalf_of=on_behalf_of,
        event_handler=events.append,
    )

    computer_context = toolkit.make_computer_context(tool_context=tool_context)

    assert computer_context.room is tool_room
    assert computer_context.caller is caller
    assert computer_context.on_behalf_of is on_behalf_of

    computer_context.emit_startup(
        state="in_progress",
        details=(" booting ",),
    )

    assert len(events) == 1
    assert events[0]["state"] == "in_progress"
    assert events[0]["details"] == ["booting"]
    assert events[0]["correlation_key"] == "context.computer:startup"

    plain_context = ToolContext(
        caller=caller,
        event_handler=events.append,
    )
    fallback_context = toolkit.make_computer_context(tool_context=plain_context)

    assert fallback_context.room is fallback_room
    assert fallback_context.caller is caller
    assert fallback_context.on_behalf_of is None

    no_room_toolkit = ComputerToolkit(
        computer=_FakeComputer(),
        operator=_FakeOperator(),
        render_screen=None,
    )
    with pytest.raises(
        RuntimeError,
        match="Toolkit 'meshagent.openai.computer' requires a bound RoomClient before use",
    ):
        no_room_toolkit.make_computer_context(tool_context=plain_context)


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
