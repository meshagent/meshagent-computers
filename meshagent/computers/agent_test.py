from typing import Any

import pytest

from meshagent.agents.images_database import SavedImage
from meshagent.computers.agent import ComputerToolkit
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
    events: list[dict[str, Any]] = []
    context = ToolContext(
        room=room,
        caller=room.local_participant,
        event_handler=lambda event: events.append(event),
    )

    computer_tool = next(tool for tool in toolkit.tools if tool.name == "computer_call")
    result = await computer_tool.handle_computer_call(
        context=context,
        type="computer_call",
        action={"type": "wait"},
    )

    assert result["type"] == "computer_call_output"
    assert len(operator.calls) == 1
    assert len(events) == 2
    assert [event["state"] for event in events] == ["in_progress", "completed"]
    assert events[0]["headline"] == "Starting browser automation session"
    assert events[1]["headline"] == "Browser automation session ready"
    assert events[0]["correlation_key"] == events[1]["correlation_key"]
