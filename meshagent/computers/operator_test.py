import pytest

from meshagent.computers.operator import Operator


class _FakeComputer:
    environment = "browser"

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def wait(self):
        self.calls.append(("wait", {}))

    async def click(self, x: int, y: int):
        self.calls.append(("click", {"x": x, "y": y}))

    async def screenshot(self) -> str:
        return "ZmFrZS1zY3JlZW5zaG90"

    async def get_current_url(self) -> str:
        return "https://example.com"


@pytest.mark.asyncio
async def test_operator_handles_batched_computer_actions():
    operator = Operator()
    computer = _FakeComputer()

    outputs = await operator.play(
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
            "acknowledged_safety_checks": [],
            "output": {
                "type": "computer_screenshot",
                "image_url": "data:image/png;base64,ZmFrZS1zY3JlZW5zaG90",
            },
        }
    ]


@pytest.mark.asyncio
async def test_operator_supports_legacy_single_action_shape():
    operator = Operator()
    computer = _FakeComputer()

    outputs = await operator.play(
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


@pytest.mark.asyncio
async def test_operator_treats_missing_pending_safety_checks_as_empty():
    operator = Operator()
    computer = _FakeComputer()

    outputs = await operator.play(
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
    assert outputs[0]["acknowledged_safety_checks"] == []


@pytest.mark.asyncio
async def test_operator_rejects_computer_call_without_action_payload():
    operator = Operator()
    computer = _FakeComputer()

    with pytest.raises(ValueError, match="missing action data"):
        await operator.play(
            computer=computer,
            item={"type": "computer_call", "call_id": "call_3"},
        )
