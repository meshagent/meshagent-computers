from typing import Any

import pytest

from meshagent.agents.images_database import SavedImage
from meshagent.computers import agent as agent_module
from meshagent.computers.agent import ComputerToolkit
from meshagent.computers.base_playwright import BasePlaywrightComputer
from meshagent.tools import ToolContext


class _FakeComputer:
    environment = "browser"
    dimensions = (1024, 768)

    async def __aenter__(self):
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


class _FakeImagesDatabase:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    async def save(
        self,
        *,
        data: bytes,
        mime_type: str,
        created_by: str,
        annotations: dict[str, str],
    ) -> SavedImage:
        self.calls.append(
            {
                "data": data,
                "mime_type": mime_type,
                "created_by": created_by,
                "annotations": annotations,
            }
        )
        return SavedImage(
            id="img_1",
            mime_type=mime_type,
            created_at="2026-02-20T00:00:00Z",
            created_by=created_by,
            annotations=annotations,
        )


class _FakeOperator:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    async def play(
        self, *, computer: _FakeComputer, item: dict[str, Any]
    ) -> list[dict]:
        self.calls.append({"computer": computer, "item": item})
        return [{"type": "computer_call_output", "output": None}]


class _FakeThreadAdapter:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def write_image(
        self,
        *,
        message_id: str | None,
        image_id: str,
        mime_type: str,
        created_at: str,
        created_by: str,
        width: int | float | None = None,
        height: int | float | None = None,
        status: str | None = None,
        status_detail: str | None = None,
    ) -> str:
        self.calls.append(
            {
                "message_id": message_id,
                "image_id": image_id,
                "mime_type": mime_type,
                "created_at": created_at,
                "created_by": created_by,
                "width": width,
                "height": height,
                "status": status,
                "status_detail": status_detail,
            }
        )
        return message_id or ""


@pytest.mark.asyncio
async def test_default_render_screen_saves_and_attaches_screenshot():
    images_db = _FakeImagesDatabase()
    adapter = _FakeThreadAdapter()
    toolkit = ComputerToolkit(
        computer=_FakeComputer(),
        room=_FakeRoom(name="agent"),
        thread_path="/threads/demo",
        thread_adapter=adapter,
        images_db=images_db,
    )
    assert toolkit.render_screen is not None

    await toolkit.render_screen(b"screen-bytes")

    assert len(images_db.calls) == 1
    save_call = images_db.calls[0]
    assert save_call["data"] == b"screen-bytes"
    assert save_call["mime_type"] == "image/png"
    assert save_call["created_by"] == "agent"
    assert save_call["annotations"] == {
        "source": "computer_toolkit",
        "thread_path": "/threads/demo",
    }

    assert len(adapter.calls) == 1
    assert adapter.calls[0]["image_id"] == "img_1"
    assert adapter.calls[0]["mime_type"] == "image/png"
    assert adapter.calls[0]["created_by"] == "agent"
    assert adapter.calls[0]["width"] == 1024
    assert adapter.calls[0]["height"] == 768
    assert adapter.calls[0]["status"] == "completed"
    assert adapter.calls[0]["status_detail"] == "Screenshot saved"
    assert isinstance(adapter.calls[0]["message_id"], str)
    assert adapter.calls[0]["message_id"] != ""


@pytest.mark.asyncio
async def test_default_render_screen_skips_without_thread_context():
    images_db = _FakeImagesDatabase()
    adapter = _FakeThreadAdapter()
    toolkit = ComputerToolkit(
        computer=_FakeComputer(),
        room=_FakeRoom(name="agent"),
        thread_path=None,
        thread_adapter=adapter,
        images_db=images_db,
    )
    assert toolkit.render_screen is not None

    await toolkit.render_screen(b"screen-bytes")

    assert images_db.calls == []
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_computer_tool_emits_startup_progress_events():
    operator = _FakeOperator()
    room = _FakeRoom(name="agent")
    toolkit = ComputerToolkit(
        computer=_FakeComputer(),
        operator=operator,
        room=room,
        render_screen=None,
    )
    context = ToolContext(room=room, caller=room.local_participant)

    computer_tool = next(tool for tool in toolkit.tools if tool.name == "computer_call")
    stream = await computer_tool.handle_computer_call(
        context=context,
        type="computer_call",
        action={"type": "wait"},
    )
    outputs: list[dict[str, Any]] = []
    async for item in stream:
        outputs.append(item)

    assert len(outputs) == 3
    assert outputs[0]["type"] == "agent.event"
    assert outputs[0]["state"] == "in_progress"
    assert outputs[1]["type"] == "agent.event"
    assert outputs[1]["state"] == "completed"
    assert outputs[2]["type"] == "computer_call_output"
    assert len(operator.calls) == 1
    assert outputs[0]["headline"] == "Starting browser automation session"
    assert outputs[1]["headline"] == "Browser automation session ready"
    assert outputs[0]["correlation_key"] == outputs[1]["correlation_key"]


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
        async def _get_browser_and_page(self):
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


def test_computer_toolkit_rejects_unsupported_dimensions():
    with pytest.raises(ValueError, match="dimensions must be one of"):
        ComputerToolkit(dimensions=(1024, 768))
