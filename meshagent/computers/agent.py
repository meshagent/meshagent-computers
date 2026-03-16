import asyncio
import base64
import inspect
import logging
import uuid
from collections.abc import Sequence
from typing import Any, Awaitable, Callable, Optional

from meshagent.agents import LLMAdapter
from meshagent.agents.images_database import ImagesDatabase
from meshagent.agents.thread_adapter import ThreadAdapter
from meshagent.tools import FunctionTool, Toolkit, ToolContext
from meshagent.agents.chat import ChatBot, ChatThreadContext
from meshagent.api import RemoteParticipant
from meshagent.openai.tools.responses_adapter import OpenAIResponsesTool
from meshagent.api import RoomClient

from .base_playwright import BasePlaywrightComputer
from .computer import Computer, ComputerContext, ComputerStartupState
from .container_playwright import ContainerPlaywrightComputer
from .local_playwright import LocalPlaywrightComputer
from .operator import Operator
from .stagehand import StagehandComputer, stagehand_available

logger = logging.getLogger("computer")
logger.setLevel(logging.WARN)

_SUPPORTED_COMPUTER_DIMENSIONS = {(1440, 900), (1600, 900)}


def _validate_computer_dimensions(
    dimensions: Optional[tuple[int, int]],
) -> None:
    if dimensions is None:
        return
    if dimensions not in _SUPPORTED_COMPUTER_DIMENSIONS:
        raise ValueError("dimensions must be one of: (1440, 900), (1600, 900)")


class ComputerTool(OpenAIResponsesTool):
    def __init__(
        self,
        *,
        operator: Operator,
        computer: Computer,
        title="computer_call",
        description="handle computer tool calls",
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
                "type": "computer",
            }
        ]

    def get_open_ai_output_handlers(self):
        return {"computer_call": self.handle_computer_call}

    async def handle_computer_call(self, context: ToolContext, **arguments):
        computer_context = self.toolkit.make_computer_context(tool_context=context)
        await self.toolkit.ensure_started_with_events(context=computer_context)

        logger.info("handling computer")
        outputs = await self.operator.play(
            computer_context,
            computer=self.computer,
            item=arguments,
        )
        if self.render_screen is not None:
            for output in outputs:
                if output["type"] == "computer_call_output":
                    if output["output"] is not None:
                        output_type = output["output"].get("type")
                        if output_type in {"input_image", "computer_screenshot"}:
                            b64: str = output["output"]["image_url"]
                            image_data_b64 = b64.split(",", 1)

                            image_bytes = base64.b64decode(image_data_b64[1])
                            render_result = self.render_screen(image_bytes)
                            if inspect.isawaitable(render_result):
                                await render_result

        return outputs[0]


class ScreenshotTool(FunctionTool):
    def __init__(self, computer: Computer, toolkit: "ComputerToolkit"):
        self.computer = computer
        self.toolkit = toolkit

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
        computer_context = self.toolkit.make_computer_context(tool_context=context)
        await self.toolkit.ensure_started_with_events(context=computer_context)
        screenshot_bytes = await self.computer.screenshot_bytes(
            computer_context,
            full_page=full_page,
        )
        await context.room.storage.upload(
            path=save_path,
            data=screenshot_bytes,
            overwrite=True,
        )

        return f"saved screenshot to {save_path}"


class GotoURL(FunctionTool):
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
        computer_context = self.toolkit.make_computer_context(tool_context=context)
        await self.toolkit.ensure_started_with_events(context=computer_context)

        if not url.startswith("https://") and not url.startswith("http://"):
            url = "https://" + url

        await self.computer.goto(computer_context, url=url)

        if self.render_screen is not None:
            render_result = self.render_screen(
                await self.computer.screenshot_bytes(
                    computer_context,
                    full_page=False,
                )
            )
            if inspect.isawaitable(render_result):
                await render_result


class ComputerToolkit(Toolkit):
    def __init__(
        self,
        *,
        name: str = "meshagent.openai.computer",
        computer: Optional[Computer] = None,
        dimensions: Optional[tuple[int, int]] = None,
        operator: Optional[Operator] = None,
        room: Optional[RoomClient] = None,
        render_screen: Optional[Callable[[bytes], Awaitable[None] | None]] = None,
        thread_path: Optional[str] = None,
        thread_adapter: Optional[ThreadAdapter] = None,
        images_db: Optional[ImagesDatabase] = None,
        include_goto_tool: bool = False,
        starting_url: str | None = None,
    ):
        _validate_computer_dimensions(dimensions)
        provided_computer = computer is not None

        if operator is None:
            operator = Operator()

        if computer is None:
            if stagehand_available():
                computer = StagehandComputer(
                    dimensions=dimensions,
                    starting_url=starting_url,
                )
            elif room is not None:
                computer = ContainerPlaywrightComputer(
                    room=room,
                    headless=True,
                    dimensions=dimensions,
                    starting_url=starting_url,
                )
            else:
                computer = LocalPlaywrightComputer(
                    dimensions=dimensions,
                    starting_url=starting_url,
                )
        elif dimensions is not None and isinstance(
            computer,
            (ContainerPlaywrightComputer, LocalPlaywrightComputer),
        ):
            computer.dimensions = dimensions

        if provided_computer and starting_url is not None:
            if isinstance(computer, BasePlaywrightComputer):
                normalized_starting_url = starting_url.strip()
                if normalized_starting_url != "":
                    computer.starting_url = normalized_starting_url
            else:
                raise ValueError("starting_url requires a Playwright computer")

        self.computer = computer
        self.operator = operator
        self.started = False
        self._starting = asyncio.Lock()
        self.room = room
        self.thread_path = thread_path
        self.thread_adapter = thread_adapter
        self._images_db = images_db
        self.include_goto_tool = include_goto_tool

        self.render_screen = (
            render_screen if render_screen is not None else self.save_screen_image
        )

        tools = [
            ComputerTool(
                computer=computer,
                operator=operator,
                render_screen=self.render_screen,
                toolkit=self,
            ),
        ]
        if include_goto_tool:
            if not isinstance(computer, BasePlaywrightComputer):
                raise ValueError("goto tool requires a Playwright computer")
            tools.append(
                GotoURL(
                    computer=computer,
                    toolkit=self,
                    render_screen=self.render_screen,
                )
            )

        super().__init__(
            name=name,
            tools=tools,
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
            width, height = self.computer.dimensions
            self.thread_adapter.write_image(
                message_id=str(uuid.uuid4()),
                image_id=saved_image.id,
                mime_type=saved_image.mime_type,
                created_at=saved_image.created_at,
                created_by=saved_image.created_by,
                width=width,
                height=height,
                status="completed",
                status_detail="Screenshot saved",
            )
        except Exception as ex:
            logger.error("failed to attach computer screenshot to thread", exc_info=ex)

    async def __aenter__(self):
        await self.ensure_started(context=self.make_bootstrap_computer_context())
        return self

    def _startup_event_key(self) -> str:
        return f"{self.name}:startup"

    def _startup_headlines(self) -> tuple[str, str, str]:
        return (
            "Starting computer...",
            "Computer ready",
            "Failed to start computer",
        )

    def make_startup_event(
        self,
        *,
        state: ComputerStartupState,
        details: Sequence[str] = (),
    ) -> dict[str, Any]:
        starting, ready, failed = self._startup_headlines()
        if state == "completed":
            headline = ready
        elif state == "failed":
            headline = failed
        else:
            headline = starting

        return {
            "type": "agent.event",
            "source": "computer",
            "name": "computer.startup",
            "kind": "tool",
            "state": state,
            "method": "computer.startup",
            "correlation_key": self._startup_event_key(),
            "summary": headline,
            "headline": headline,
            "details": list(details),
        }

    def make_computer_context(self, *, tool_context: ToolContext) -> ComputerContext:
        return ComputerContext(
            room=tool_context.room,
            caller=tool_context.caller,
            on_behalf_of=tool_context.on_behalf_of,
            caller_context=tool_context.caller_context,
            event_handler=tool_context.emit,
            startup_event_factory=lambda state, details: self.make_startup_event(
                state=state,
                details=details,
            ),
        )

    def make_bootstrap_computer_context(self) -> ComputerContext:
        if self.room is None:
            raise RuntimeError(
                "ComputerToolkit startup requires a room-backed ComputerContext"
            )
        return ComputerContext(
            room=self.room,
            caller=self.room.local_participant,
            startup_event_factory=lambda state, details: self.make_startup_event(
                state=state,
                details=details,
            ),
        )

    async def ensure_started(self, *, context: ComputerContext) -> bool:
        if self.started:
            return False

        async with self._starting:
            if self.started:
                return False

            await self.computer.__aenter__(context)
            self.started = True
            return True

    async def ensure_started_with_events(
        self,
        *,
        context: ComputerContext,
    ) -> bool:
        emit_startup_status = not self.started
        if emit_startup_status:
            context.emit_startup(state="in_progress")

        try:
            started = await self.ensure_started(context=context)
        except Exception:
            if emit_startup_status and context.last_startup_state not in {
                "failed",
                "cancelled",
            }:
                context.emit_startup(state="failed")
            raise

        if emit_startup_status and context.last_startup_state != "completed":
            context.emit_startup(state="completed")

        return started

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
        dimensions: Optional[tuple[int, int]] = None,
        include_goto_tool: Optional[bool] = None,
        starting_url: str | None = None,
    ):
        if rules is None:
            rules = []
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
        self.computer_dimensions: Optional[tuple[int, int]] = dimensions
        self.include_goto_tool: Optional[bool] = include_goto_tool
        self.starting_url: str | None = starting_url

    async def make_operator(self) -> Operator:
        return Operator()

    async def make_computer(self) -> Computer:
        if stagehand_available():
            return StagehandComputer(
                dimensions=self.computer_dimensions,
                starting_url=self.starting_url,
            )

        return ContainerPlaywrightComputer(
            room=self.room,
            dimensions=self.computer_dimensions,
            starting_url=self.starting_url,
        )

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
            dimensions=self.computer_dimensions,
            room=self.room,
            thread_path=thread_context.path,
            thread_adapter=thread_adapter,
            include_goto_tool=self.include_goto_tool or False,
            starting_url=self.starting_url,
        )

        return [computer_toolkit, *toolkits]
