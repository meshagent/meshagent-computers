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

    async def click(
        self, context: ComputerContext, *, x: int, y: int, button: str = "left"
    ):
        self._record_context(context)
        self.calls.append(("click", {"x": x, "y": y, "button": button}))

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
        ("click", {"x": 10, "y": 20, "button": "left"}),
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
async def test_operator_ignores_unsupported_action_arguments():
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
            "call_id": "call_extra_args",
            "action": {"type": "click", "x": 10, "y": 20, "keys": ["CTRL"]},
            "pending_safety_checks": [],
        },
    )

    assert computer.calls == [("click", {"x": 10, "y": 20, "button": "left"})]
    assert outputs[0]["type"] == "computer_call_output"


@pytest.mark.asyncio
async def test_operator_function_call_preserves_method_defaults_and_filters_arguments():
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
            "type": "function_call",
            "name": "click",
            "arguments": '{"x": 10, "y": 20, "ignored": true}',
            "call_id": "call_function",
        },
    )

    assert computer.calls == [("click", {"x": 10, "y": 20, "button": "left"})]
    assert outputs == [
        {
            "type": "function_call_output",
            "call_id": "call_function",
            "output": "success",
        }
    ]


@pytest.mark.asyncio
async def test_operator_function_call_non_object_arguments_use_python_items_error():
    operator = Operator()
    computer = _FakeComputer()
    context = ComputerContext(
        room=object(),  # type: ignore[arg-type]
        caller=object(),  # type: ignore[arg-type]
    )

    with pytest.raises(AttributeError, match="'list' object has no attribute 'items'"):
        await operator.play(
            context,
            computer=computer,
            item={
                "type": "function_call",
                "name": "wait",
                "arguments": "[]",
                "call_id": "call_function_non_object",
            },
        )


@pytest.mark.asyncio
async def test_operator_function_call_invalid_json_arguments_errors_match_python():
    operator = Operator()
    computer = _FakeComputer()
    context = ComputerContext(
        room=object(),  # type: ignore[arg-type]
        caller=object(),  # type: ignore[arg-type]
    )

    cases = [
        ("", "Expecting value: line 1 column 1 \\(char 0\\)"),
        ("not json", "Expecting value: line 1 column 1 \\(char 0\\)"),
        ("{", "Expecting property name enclosed in double quotes: line 1 column 2"),
        ('{"ms":}', "Expecting value: line 1 column 7 \\(char 6\\)"),
        ('{"ms": 1,}', "Illegal trailing comma before end of object: line 1 column 9"),
    ]

    for arguments, expected in cases:
        with pytest.raises(ValueError, match=expected):
            await operator.play(
                context,
                computer=computer,
                item={
                    "type": "function_call",
                    "name": "wait",
                    "arguments": arguments,
                    "call_id": "call_invalid_json",
                },
            )


@pytest.mark.asyncio
async def test_operator_function_call_direct_indexing_errors_match_python():
    operator = Operator()
    computer = _FakeComputer()
    context = ComputerContext(
        room=object(),  # type: ignore[arg-type]
        caller=object(),  # type: ignore[arg-type]
    )

    with pytest.raises(KeyError, match="name"):
        await operator.play(
            context,
            computer=computer,
            item={
                "type": "function_call",
                "arguments": "{}",
                "call_id": "call_missing_name",
            },
        )

    with pytest.raises(KeyError, match="arguments"):
        await operator.play(
            context,
            computer=computer,
            item={
                "type": "function_call",
                "name": "wait",
                "call_id": "call_missing_arguments",
            },
        )

    with pytest.raises(
        TypeError,
        match="the JSON object must be str, bytes or bytearray, not int",
    ):
        await operator.play(
            context,
            computer=computer,
            item={
                "type": "function_call",
                "name": "wait",
                "arguments": 123,
                "call_id": "call_non_string_arguments",
            },
        )

    with pytest.raises(ValueError, match="unsupported computer action: 123"):
        await operator.play(
            context,
            computer=computer,
            item={
                "type": "function_call",
                "name": 123,
                "arguments": "{}",
                "call_id": "call_non_string_name",
            },
        )

    assert (
        await operator.play(
            context,
            computer=computer,
            item={
                "type": 123,
                "name": "wait",
                "arguments": "{}",
                "call_id": "call_non_string_type",
            },
        )
        == []
    )


@pytest.mark.asyncio
async def test_operator_play_non_mapping_item_type_errors_match_python():
    operator = Operator()
    computer = _FakeComputer()
    context = ComputerContext(
        room=object(),  # type: ignore[arg-type]
        caller=object(),  # type: ignore[arg-type]
    )

    with pytest.raises(TypeError, match="list indices must be integers"):
        await operator.play(context, computer=computer, item=[])  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="'NoneType' object is not subscriptable"):
        await operator.play(context, computer=computer, item=None)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="string indices must be integers"):
        await operator.play(context, computer=computer, item="abc")  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="'int' object is not subscriptable"):
        await operator.play(context, computer=computer, item=123)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_operator_function_call_missing_call_id_fails_after_action():
    operator = Operator()
    computer = _FakeComputer()
    context = ComputerContext(
        room=object(),  # type: ignore[arg-type]
        caller=object(),  # type: ignore[arg-type]
    )

    with pytest.raises(KeyError, match="call_id"):
        await operator.play(
            context,
            computer=computer,
            item={
                "type": "function_call",
                "name": "wait",
                "arguments": "{}",
            },
        )

    assert computer.calls == [("wait", {})]


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
async def test_operator_pending_safety_check_message_indexing_matches_python():
    operator = Operator()
    computer = _FakeComputer()
    context = ComputerContext(
        room=object(),  # type: ignore[arg-type]
        caller=object(),  # type: ignore[arg-type]
    )

    with pytest.raises(KeyError, match="message"):
        await operator.play(
            context,
            computer=computer,
            item={
                "type": "computer_call",
                "call_id": "call_missing_message",
                "action": {"type": "wait"},
                "pending_safety_checks": [{}],
            },
        )

    with pytest.raises(TypeError, match="list indices must be integers"):
        await operator.play(
            context,
            computer=computer,
            item={
                "type": "computer_call",
                "call_id": "call_list_check",
                "action": {"type": "wait"},
                "pending_safety_checks": [[]],
            },
        )

    outputs = await operator.play(
        context,
        computer=computer,
        item={
            "type": "computer_call",
            "call_id": "call_non_string_message",
            "action": {"type": "wait"},
            "pending_safety_checks": [{"message": 123}, {"message": None}],
        },
    )

    assert outputs[0]["type"] == "computer_call_output"


@pytest.mark.asyncio
async def test_operator_computer_call_missing_call_id_fails_after_action():
    operator = Operator()
    computer = _FakeComputer()
    context = ComputerContext(
        room=object(),  # type: ignore[arg-type]
        caller=object(),  # type: ignore[arg-type]
    )

    with pytest.raises(KeyError, match="call_id"):
        await operator.play(
            context,
            computer=computer,
            item={
                "type": "computer_call",
                "action": {"type": "wait"},
                "pending_safety_checks": [],
            },
        )

    assert computer.calls == [("wait", {})]


@pytest.mark.asyncio
async def test_operator_computer_call_action_type_indexing_matches_python():
    operator = Operator()
    computer = _FakeComputer()
    context = ComputerContext(
        room=object(),  # type: ignore[arg-type]
        caller=object(),  # type: ignore[arg-type]
    )

    with pytest.raises(KeyError, match="type"):
        await operator.play(
            context,
            computer=computer,
            item={
                "type": "computer_call",
                "call_id": "call_missing_action_type",
                "action": {},
                "pending_safety_checks": [],
            },
        )

    with pytest.raises(ValueError, match="unsupported computer action: 123"):
        await operator.play(
            context,
            computer=computer,
            item={
                "type": "computer_call",
                "call_id": "call_non_string_action_type",
                "action": {"type": 123},
                "pending_safety_checks": [],
            },
        )


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
