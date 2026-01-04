from meshagent.agents import LLMAdapter
from meshagent.tools import Tool, Toolkit, ToolContext
from meshagent.computers import (
    Computer,
    Operator,
    ContainerPlaywrightComputer,
    LocalPlaywrightComputer,
)
from meshagent.agents.chat import ChatBot, ChatThreadContext
from meshagent.api import RemoteParticipant
from meshagent.openai.tools.responses_adapter import OpenAIResponsesTool
from meshagent.api import RoomClient
from typing import Optional, Callable
import base64
import logging

logger = logging.getLogger("computer")
logger.setLevel(logging.WARN)


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
        render_screen: Optional[Callable] = None,
        toolkit: "ComputerToolkit",
    ):
        super().__init__(
            name="computer_call",
            # TODO: give a correct schema
            title=title,
            description=description,
            rules=rules,
            thumbnail_url=thumbnail_url,
        )
        self.operator = operator
        self.computer = computer
        self.render_screen = render_screen
        self.toolkit = toolkit

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
        if not self.toolkit.started:
            await self.toolkit.__aenter__()

        logger.info("handling computer")
        outputs = await self.operator.play(computer=self.computer, item=arguments)
        if self.render_screen is not None:
            for output in outputs:
                if output["type"] == "computer_call_output":
                    if output["output"] is not None:
                        if output["output"]["type"] == "input_image":
                            b64: str = output["output"]["image_url"]
                            image_data_b64 = b64.split(",", 1)

                            image_bytes = base64.b64decode(image_data_b64[1])
                            self.render_screen(image_bytes)

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
    def __init__(
        self,
        computer: Computer,
        toolkit: "ComputerToolkit",
        render_screen: Optional[Callable] = None,
    ):
        self.computer = computer
        self.render_screen = render_screen
        self.toolkit = toolkit

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
        if not self.toolkit.started:
            await self.toolkit.__aenter__()

        if not url.startswith("https://") and not url.startswith("http://"):
            url = "https://" + url

        await self.computer.goto(url)

        if self.render_screen is not None:
            self.render_screen(await self.computer.screenshot_bytes(full_page=False))


class ComputerToolkit(Toolkit):
    def __init__(
        self,
        *,
        name: str = "meshagent.openai.computer",
        computer: Optional[Computer] = None,
        operator: Optional[Operator] = None,
        room: Optional[RoomClient] = None,
        render_screen: Optional[Callable] = None,
    ):
        if operator is None:
            operator = Operator()

        if computer is None:
            if room is not None:
                computer = ContainerPlaywrightComputer(
                    room=room,
                    headless=True,
                )

            else:
                computer = LocalPlaywrightComputer()

        self.computer = computer
        self.operator = operator
        self.started = False
        self._starting = None

        self.render_screen = render_screen

        super().__init__(
            name=name,
            tools=[
                ComputerTool(
                    computer=computer,
                    operator=operator,
                    render_screen=render_screen,
                    toolkit=self,
                ),
                # ScreenshotTool(computer=computer),
                GotoURL(computer=computer, toolkit=self, render_screen=render_screen),
            ],
        )

    async def __aenter__(self):
        self.started = False

        if not self.started:
            self.started = True
            await self.computer.__aenter__()

    async def __aexit__(self):
        if self.started:
            self.started = False
            await self.computer.__aexit__(None, None, None)


class ComputerChatBot(ChatBot):
    def __init__(
        self,
        *,
        name,
        title=None,
        description=None,
        requires=None,
        labels=None,
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

    async def make_operator(self) -> Operator:
        return Operator()

    async def make_computer(self) -> Computer:
        return ContainerPlaywrightComputer(room=self.room)

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

        computer_toolkit = ComputerToolkit(
            operator=self.operator,
            computer=self.computer,
            render_screen=render_screen,
        )

        await computer_toolkit.ensure_started()

        return [computer_toolkit, *toolkits]
