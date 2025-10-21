from meshagent.agents import LLMAdapter
from meshagent.tools import Tool, Toolkit, ToolContext
from meshagent.computers import Computer, Operator, BrowserbaseBrowser
from meshagent.agents.chat import ChatBot, ChatThreadContext
from meshagent.api import RemoteParticipant
from meshagent.openai.tools.responses_adapter import OpenAIResponsesTool

from typing import Optional, Type, Callable
import base64
import logging

logger = logging.getLogger("computer")
logger.setLevel(logging.WARN)


class ComputerToolkit(Toolkit):
    def __init__(
        self, name: str, computer: Computer, operator: Operator, tools: list[Tool]
    ):
        super().__init__(name=name, tools=tools)
        self.computer = computer
        self.operator = operator
        self.started = False
        self._starting = None

    async def ensure_started(self):
        self.started = False

        if not self.started:
            self.started = True
            await self.computer.__aenter__()


def make_computer_toolkit(
    *,
    operator_cls: Type[Operator],
    computer_cls: Type[Computer],
    render_screen: Callable[[bytes], None],
):
    operator = operator_cls()
    computer = computer_cls()

    class ComputerTool(OpenAIResponsesTool):
        def __init__(
            self,
            *,
            operator: Operator,
            computer: Computer,
            title="computer_call",
            description="handle computer calls from computer use preview",
            rules=[],
            thumbnail_url=None,
        ):
            super().__init__(
                name="computer_call",
                # TODO: give a correct schema
                title=title,
                description=description,
                rules=rules,
                thumbnail_url=thumbnail_url,
            )
            self.computer = computer

        def get_open_ai_tool_definitions(self) -> list[dict]:
            return [
                {
                    "type": "computer_use_preview",
                    "display_width": self.computer.dimensions[0],
                    "display_height": self.computer.dimensions[1],
                    "environment": self.computer.environment,
                }
            ]

        def get_open_ai_output_handlers(self):
            return {"computer_call": self.handle_computer_call}

        async def handle_computer_call(self, context: ToolContext, **arguments):
            outputs = await operator.play(computer=self.computer, item=arguments)
            for output in outputs:
                if output["type"] == "computer_call_output":
                    if output["output"] is not None:
                        if output["output"]["type"] == "input_image":
                            b64: str = output["output"]["image_url"]
                            image_data_b64 = b64.split(",", 1)

                            image_bytes = base64.b64decode(image_data_b64[1])
                            render_screen(image_bytes)

            nonlocal computer_toolkit
            if len(computer_toolkit.tools) == 1:
                # HACK: after looking at the page, add the other tools,
                # if we add these first then the computer-use-preview mode fails if it calls them before using the computer
                computer_toolkit.tools.extend(
                    [
                        ScreenshotTool(computer=computer),
                        GotoURL(computer=computer),
                    ]
                )
            return outputs[0]

    class ScreenshotTool(Tool):
        def __init__(self, computer: Computer):
            self.computer = computer

            super().__init__(
                name="screenshot",
                # TODO: give a correct schema
                input_schema={
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
                },
                description="take a screenshot of the current page",
            )

        async def execute(self, context: ToolContext, save_path: str, full_page: bool):
            screenshot_bytes = await self.computer.screenshot_bytes(full_page=full_page)
            handle = await context.room.storage.open(path=save_path, overwrite=True)
            await context.room.storage.write(handle=handle, data=screenshot_bytes)
            await context.room.storage.close(handle=handle)

            return f"saved screenshot to {save_path}"

    class GotoURL(Tool):
        def __init__(self, computer: Computer):
            self.computer = computer

            super().__init__(
                name="goto",
                description="goes to a specific URL. Make sure it starts with http:// or https://",
                # TODO: give a correct schema
                input_schema={
                    "additionalProperties": False,
                    "type": "object",
                    "required": ["url"],
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Fully qualified URL to navigate to.",
                        }
                    },
                },
            )

        async def execute(self, context: ToolContext, url: str):
            if not url.startswith("https://") and not url.startswith("http://"):
                url = "https://" + url

            await self.computer.goto(url)

            render_screen(await self.computer.screenshot_bytes(full_page=False))

    computer_tool = ComputerTool(computer=computer, operator=operator)

    computer_toolkit = ComputerToolkit(
        name="meshagent.openai.computer",
        computer=computer,
        operator=operator,
        tools=[computer_tool],
    )

    return computer_toolkit


class ComputerAgent(ChatBot):
    def __init__(
        self,
        *,
        name,
        title=None,
        description=None,
        requires=None,
        labels=None,
        computer_cls: Type[Computer] = BrowserbaseBrowser,
        operator_cls: Type[Operator] = Operator,
        rules: Optional[list[str]] = None,
        llm_adapter: Optional[LLMAdapter] = None,
        toolkits: list[Toolkit] = None,
    ):
        if rules is None:
            rules = [
                "if asked to go to a URL, you MUST use the goto function to go to the url if it is available",
                "after going directly to a URL, the screen will change so you should take a look at it to know what to do next",
            ]
        super().__init__(
            name=name,
            title=title,
            description=description,
            requires=requires,
            labels=labels,
            llm_adapter=llm_adapter,
            toolkits=toolkits,
            rules=rules,
        )
        self.computer_cls = computer_cls
        self.operator_cls = operator_cls

    async def get_thread_toolkits(
        self, *, thread_context: ChatThreadContext, participant: RemoteParticipant
    ):
        toolkits = await super().get_thread_toolkits(
            thread_context=thread_context, participant=participant
        )

        def render_screen(image_bytes: bytes):
            for participant in thread_context.participants:
                self.room.messaging.send_message_nowait(
                    to=participant,
                    type="computer_screen",
                    message={},
                    attachment=image_bytes,
                )

        computer_toolkit = make_computer_toolkit(
            operator_cls=self.operator_cls,
            computer_cls=self.computer_cls,
            render_screen=render_screen,
        )

        await computer_toolkit.ensure_started()

        return [computer_toolkit, *toolkits]
