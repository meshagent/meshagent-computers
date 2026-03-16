import pytest

from meshagent.computers.computer import ComputerContext
from meshagent.computers.operator import Operator


class _FakeComputer:
    environment = "browser"

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.contexts: list[ComputerContext] = []

    def _record_context(self, context: ComputerContext) -> None:
        self.contexts.append(context)

    async def wait(self, context: ComputerContext):
        self._record_context(context)
        self.calls.append(("wait", {}))

    async def click(self, context: ComputerContext, *, x: int, y: int):
        self._record_context(context)
        self.calls.append(("click", {"x": x, "y": y}))

    async def double_click(
        self,
        context: ComputerContext,
        *,
        x: int,
        y: int,
    ):
        self._record_context(context)
        self.calls.append(("double_click", {"x": x, "y": y}))

    async def scroll(
        self,
        context: ComputerContext,
        *,
        x: int,
        y: int,
        scroll_x: int,
        scroll_y: int,
    ):
        self._record_context(context)
        self.calls.append(
            ("scroll", {"x": x, "y": y, "scroll_x": scroll_x, "scroll_y": scroll_y})
        )

    async def type(self, context: ComputerContext, *, text: str):
        self._record_context(context)
        self.calls.append(("type", {"text": text}))

    async def move(
        self,
        context: ComputerContext,
        *,
        x: int,
        y: int,
    ):
        self._record_context(context)
        self.calls.append(("move", {"x": x, "y": y}))

    async def keypress(
        self,
        context: ComputerContext,
        *,
        keys: list[str],
    ):
        self._record_context(context)
        self.calls.append(("keypress", {"keys": keys}))

    async def drag(
        self,
        context: ComputerContext,
        *,
        path: list[dict[str, int]],
    ):
        self._record_context(context)
        self.calls.append(("drag", {"path": path}))

    async def screenshot(self, context: ComputerContext) -> str:
        self._record_context(context)
        return "ZmFrZS1zY3JlZW5zaG90"

    async def get_current_url(self, context: ComputerContext) -> str:
        self._record_context(context)
        return "https://example.com"

    async def goto(self, context: ComputerContext, *, url: str):
        self._record_context(context)
        self.calls.append(("goto", {"url": url}))

    async def back(self, context: ComputerContext):
        self._record_context(context)
        self.calls.append(("back", {}))

    async def forward(self, context: ComputerContext):
        self._record_context(context)
        self.calls.append(("forward", {}))


@pytest.mark.asyncio
async def test_operator_handles_batched_computer_actions():
    operator = Operator()
    computer = _FakeComputer()
    context = ComputerContext(
        room=object(),  # type: ignore[arg-type]
        caller=object(),  # type: ignore[arg-type]
    )

    outputs = await operator.play(
        context,
        computer=computer,
        item={
            "type": "computer_call",
            "call_id": "call_1",
            "actions": [
                {"type": "wait"},
                {"type": "click", "x": 10, "y": 20},
            ],
            "pending_safety_checks": [],
        },
    )

    assert computer.calls == [
        ("wait", {}),
        ("click", {"x": 10, "y": 20}),
    ]
    assert outputs == [
        {
            "type": "computer_call_output",
            "call_id": "call_1",
            "output": {
                "type": "computer_screenshot",
                "image_url": "data:image/png;base64,ZmFrZS1zY3JlZW5zaG90",
            },
        }
    ]
    assert computer.contexts == [context, context, context, context]


@pytest.mark.asyncio
async def test_operator_supports_legacy_single_action_shape():
    operator = Operator()
    computer = _FakeComputer()
    context = ComputerContext(
        room=object(),  # type: ignore[arg-type]
        caller=object(),  # type: ignore[arg-type]
    )

    outputs = await operator.play(
        context,
        computer=computer,
        item={
            "type": "computer_call",
            "call_id": "call_2",
            "action": {"type": "wait"},
            "pending_safety_checks": [],
        },
    )

    assert computer.calls == [("wait", {})]
    assert outputs[0]["type"] == "computer_call_output"
    assert outputs[0]["call_id"] == "call_2"
    assert "acknowledged_safety_checks" not in outputs[0]


@pytest.mark.asyncio
async def test_operator_treats_missing_pending_safety_checks_as_empty():
    operator = Operator()
    computer = _FakeComputer()
    context = ComputerContext(
        room=object(),  # type: ignore[arg-type]
        caller=object(),  # type: ignore[arg-type]
    )

    outputs = await operator.play(
        context,
        computer=computer,
        item={
            "type": "computer_call",
            "call_id": "call_no_checks",
            "action": {"type": "wait"},
            "pending_safety_checks": None,
        },
    )

    assert computer.calls == [("wait", {})]
    assert outputs[0]["type"] == "computer_call_output"
    assert "acknowledged_safety_checks" not in outputs[0]


@pytest.mark.asyncio
async def test_operator_rejects_computer_call_without_action_payload():
    operator = Operator()
    computer = _FakeComputer()
    context = ComputerContext(
        room=object(),  # type: ignore[arg-type]
        caller=object(),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="missing action data"):
        await operator.play(
            context,
            computer=computer,
            item={"type": "computer_call", "call_id": "call_3"},
        )
