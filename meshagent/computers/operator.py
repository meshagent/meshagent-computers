from .computer import Computer
from .utils import check_blocklisted_url
import json


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

    async def play(self, *, computer: Computer, item: dict) -> list:
        """Handle each item; may cause a computer action + screenshot."""
        if item["type"] == "function_call":
            name, args = item["name"], json.loads(item["arguments"])

            if hasattr(computer, name):  # if function exists on computer, call it
                method = getattr(computer, name)
                await method(**args)
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
                method = getattr(computer, action_type)
                await method(**action_args)

            screenshot_base64 = await computer.screenshot()
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
                "acknowledged_safety_checks": pending_checks,
                "output": {
                    "type": "computer_screenshot",
                    "image_url": f"data:image/png;base64,{screenshot_base64}",
                },
            }

            # additional URL safety checks for browser environments
            if computer.environment == "browser":
                current_url = await computer.get_current_url()
                check_blocklisted_url(current_url)

            return [call_output]
        return []
