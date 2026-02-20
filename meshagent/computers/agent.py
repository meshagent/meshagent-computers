import asyncio
import base64
import inspect
import logging
import uuid
from typing import Awaitable, Callable, Optional

from meshagent.agents import LLMAdapter
from meshagent.agents.images_database import ImagesDatabase
from meshagent.agents.thread_adapter import ThreadAdapter
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
        render_screen: Optional[Callable[[bytes], Awaitable[None] | None]] = None,
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
                            render_result = self.render_screen(image_bytes)
                            if inspect.isawaitable(render_result):
                                await render_result

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
        render_screen: Optional[Callable[[bytes], Awaitable[None] | None]] = None,
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
            render_result = self.render_screen(
                await self.computer.screenshot_bytes(full_page=False)
            )
            if inspect.isawaitable(render_result):
                await render_result


class ComputerToolkit(Toolkit):
    def __init__(
        self,
        *,
        name: str = "meshagent.openai.computer",
        computer: Optional[Computer] = None,
        operator: Optional[Operator] = None,
        room: Optional[RoomClient] = None,
        render_screen: Optional[Callable[[bytes], Awaitable[None] | None]] = None,
        thread_path: Optional[str] = None,
        thread_adapter: Optional[ThreadAdapter] = None,
        images_db: Optional[ImagesDatabase] = None,
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
        self._starting = asyncio.Lock()
        self.room = room
        self.thread_path = thread_path
        self.thread_adapter = thread_adapter
        self._images_db = images_db

        self.render_screen = (
            render_screen if render_screen is not None else self.save_screen_image
        )

        super().__init__(
            name=name,
            tools=[
                ComputerTool(
                    computer=computer,
                    operator=operator,
                    render_screen=self.render_screen,
                    toolkit=self,
                ),
                # ScreenshotTool(computer=computer),
                GotoURL(
                    computer=computer,
                    toolkit=self,
                    render_screen=self.render_screen,
                ),
            ],
        )

    async def save_screen_image(self, image_bytes: bytes) -> None:
        if self.room is None:
            return
        if not isinstance(self.thread_path, str) or self.thread_path.strip() == "":
            return
        if self.thread_adapter is None:
            logger.warning(
                "thread adapter was not available for screenshot persistence",
                extra={"path": self.thread_path},
            )
            return

        created_by = self.room.local_participant.get_attribute("name")
        if not isinstance(created_by, str):
            created_by = ""

        if self._images_db is None:
            self._images_db = ImagesDatabase(room=self.room)

        try:
            saved_image = await self._images_db.save(
                data=image_bytes,
                mime_type="image/png",
                created_by=created_by,
                annotations={
                    "source": "computer_toolkit",
                    "thread_path": self.thread_path,
                },
            )
        except Exception as ex:
            logger.error("failed to persist computer screenshot", exc_info=ex)
            return

        try:
            self.thread_adapter.write_image(
                message_id=str(uuid.uuid4()),
                image_id=saved_image.id,
                mime_type=saved_image.mime_type,
                created_at=saved_image.created_at,
                created_by=saved_image.created_by,
                status="completed",
                status_detail="Screenshot saved",
            )
        except Exception as ex:
            logger.error("failed to attach computer screenshot to thread", exc_info=ex)

    async def __aenter__(self):
        await self.ensure_started()
        return self

    async def ensure_started(self):
        if self.started:
            return

        async with self._starting:
            if self.started:
                return

            await self.computer.__aenter__()
            self.started = True

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
        annotations=None,
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
            annotations=annotations,
            llm_adapter=llm_adapter,
            toolkits=toolkits,
            rules=rules,
        )
        self.operator: Optional[Operator] = None
        self.computer: Optional[Computer] = None

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

        if self.operator is None:
            self.operator = await self.make_operator()
        if self.computer is None:
            self.computer = await self.make_computer()

        thread_adapter = self._open_threads.get(thread_context.path)
        if not isinstance(thread_adapter, ThreadAdapter):
            thread_adapter = None

        computer_toolkit = ComputerToolkit(
            operator=self.operator,
            computer=self.computer,
            room=self.room,
            thread_path=thread_context.path,
            thread_adapter=thread_adapter,
        )

        await computer_toolkit.ensure_started()

        return [computer_toolkit, *toolkits]
