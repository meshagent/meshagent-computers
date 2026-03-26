from collections.abc import Awaitable, Callable
import inspect
import json
import logging

from .computer import Computer, ComputerContext
from .utils import check_blocklisted_url

logger = logging.getLogger(__name__)


class Operator:
    def __init__(self):
        self.show_images = False

    async def acknowledge_safety_check_callback(self, data: dict):
        return True

    async def show_image(self, base_64: str):
        pass

    @staticmethod
    def _extract_computer_actions(item: dict) -> list[dict]:
        actions = item.get("actions")
        if isinstance(actions, list) and len(actions) > 0:
            for action in actions:
                if not isinstance(action, dict):
                    raise ValueError("computer_call actions must be objects")
            return actions

        action = item.get("action")
        if isinstance(action, dict):
            return [action]

        raise ValueError("computer_call is missing action data")

    @staticmethod
    def _resolve_computer_method(
        *, computer: Computer, action_type: str
    ) -> Callable[..., Awaitable[object]]:
        methods: dict[str, Callable[..., Awaitable[object]]] = {
            "screenshot": computer.screenshot,
            "click": computer.click,
            "double_click": computer.double_click,
            "scroll": computer.scroll,
            "type": computer.type,
            "wait": computer.wait,
            "move": computer.move,
            "keypress": computer.keypress,
            "drag": computer.drag,
            "get_current_url": computer.get_current_url,
            "goto": computer.goto,
            "back": computer.back,
            "forward": computer.forward,
        }
        method = methods.get(action_type)
        if method is None:
            raise ValueError(f"unsupported computer action: {action_type}")
        return method

    @staticmethod
    def _filter_action_args(
        *,
        method: Callable[..., Awaitable[object]],
        action_type: str,
        action_args: dict[str, object],
    ) -> dict[str, object]:
        signature = inspect.signature(method)
        allowed_names: set[str] = set()

        for name, parameter in signature.parameters.items():
            if name == "context":
                continue
            if parameter.kind == inspect.Parameter.VAR_KEYWORD:
                return action_args
            if parameter.kind in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ):
                allowed_names.add(name)

        filtered_args = {
            name: value for name, value in action_args.items() if name in allowed_names
        }
        dropped_args = sorted(name for name in action_args if name not in allowed_names)
        if dropped_args:
            logger.warning(
                "ignoring unsupported arguments for computer action %s: %s",
                action_type,
                ", ".join(dropped_args),
            )
        return filtered_args

    async def play(
        self,
        context: ComputerContext,
        *,
        computer: Computer,
        item: dict,
    ) -> list:
        """Handle each item; may cause a computer action + screenshot."""
        if item["type"] == "function_call":
            name, args = item["name"], json.loads(item["arguments"])
            method = self._resolve_computer_method(
                computer=computer,
                action_type=name,
            )
            await method(
                context,
                **self._filter_action_args(
                    method=method,
                    action_type=name,
                    action_args=args,
                ),
            )
            return [
                {
                    "type": "function_call_output",
                    "call_id": item["call_id"],
                    "output": "success",  # hard-coded output for demo
                }
            ]

        if item["type"] == "computer_call":
            actions = self._extract_computer_actions(item)
            for action in actions:
                action_type = action["type"]
                action_args = {k: v for k, v in action.items() if k != "type"}
                method = self._resolve_computer_method(
                    computer=computer,
                    action_type=action_type,
                )
                await method(
                    context,
                    **self._filter_action_args(
                        method=method,
                        action_type=action_type,
                        action_args=action_args,
                    ),
                )

            screenshot_base64 = await computer.screenshot(context)
            if self.show_images:
                await self.show_image(screenshot_base64)

            # if user doesn't ack all safety checks exit with error
            pending_checks_value = item.get("pending_safety_checks")
            pending_checks: list[dict] = []
            if isinstance(pending_checks_value, list):
                pending_checks = pending_checks_value
            for check in pending_checks:
                message = check["message"]
                if not await self.acknowledge_safety_check_callback(message):
                    raise ValueError(
                        f"Safety check failed: {message}. Cannot continue with unacknowledged safety checks."
                    )

            call_output = {
                "type": "computer_call_output",
                "call_id": item["call_id"],
                "output": {
                    "type": "computer_screenshot",
                    "image_url": f"data:image/png;base64,{screenshot_base64}",
                },
            }

            # additional URL safety checks for browser environments
            if computer.environment == "browser":
                current_url = await computer.get_current_url(context)
                check_blocklisted_url(current_url)

            return [call_output]
        return []
