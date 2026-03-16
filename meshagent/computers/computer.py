from collections.abc import Callable, Sequence
from typing import Any, Dict, List, Literal, Optional, Protocol

from meshagent.api.participant import Participant
from meshagent.api.room_server_client import RoomClient

ComputerStartupState = Literal[
    "queued",
    "in_progress",
    "completed",
    "failed",
    "cancelled",
]


class ComputerContext:
    def __init__(
        self,
        *,
        room: RoomClient,
        caller: Participant,
        on_behalf_of: Optional[Participant] = None,
        caller_context: Optional[Dict[str, Any]] = None,
        event_handler: Optional[Callable[[dict[str, Any]], None]] = None,
        startup_event_factory: Optional[
            Callable[
                [ComputerStartupState, tuple[str, ...]],
                dict[str, Any],
            ]
        ] = None,
    ) -> None:
        self._room = room
        self._caller = caller
        self._on_behalf_of = on_behalf_of
        self._caller_context = caller_context
        self._event_handler = event_handler
        self._startup_event_factory = startup_event_factory
        self._last_startup_signature: (
            tuple[ComputerStartupState, tuple[str, ...]] | None
        ) = None
        self._last_startup_state: ComputerStartupState | None = None
        self._last_startup_details: tuple[str, ...] = ()

    @property
    def room(self) -> RoomClient:
        return self._room

    @property
    def caller(self) -> Participant:
        return self._caller

    @property
    def on_behalf_of(self) -> Optional[Participant]:
        return self._on_behalf_of

    @property
    def caller_context(self) -> Optional[Dict[str, Any]]:
        return self._caller_context

    @property
    def last_startup_state(self) -> ComputerStartupState | None:
        return self._last_startup_state

    @property
    def last_startup_details(self) -> tuple[str, ...]:
        return self._last_startup_details

    def emit(self, event: dict[str, Any]) -> None:
        if self._event_handler is not None:
            self._event_handler(event)

    def emit_startup(
        self,
        *,
        state: ComputerStartupState,
        details: Sequence[str] = (),
    ) -> None:
        if self._startup_event_factory is None:
            return

        normalized_details = tuple(
            detail.strip()
            for detail in details
            if isinstance(detail, str) and detail.strip() != ""
        )
        signature = (state, normalized_details)
        if signature == self._last_startup_signature:
            return

        self._last_startup_signature = signature
        self._last_startup_state = state
        self._last_startup_details = normalized_details
        self.emit(self._startup_event_factory(state, normalized_details))


class Computer(Protocol):
    """Defines the 'shape' (methods/properties) our loop expects."""

    @property
    def environment(self) -> Literal["windows", "mac", "linux", "browser"]: ...
    @property
    def dimensions(self) -> tuple[int, int]: ...

    async def screenshot(self, context: ComputerContext) -> str: ...

    async def click(
        self,
        context: ComputerContext,
        *,
        x: int,
        y: int,
        button: str = "left",
    ) -> None: ...

    async def double_click(
        self,
        context: ComputerContext,
        *,
        x: int,
        y: int,
    ) -> None: ...

    async def scroll(
        self,
        context: ComputerContext,
        *,
        x: int,
        y: int,
        scroll_x: int,
        scroll_y: int,
    ) -> None: ...

    async def type(
        self,
        context: ComputerContext,
        *,
        text: str,
    ) -> None: ...

    async def wait(
        self,
        context: ComputerContext,
        *,
        ms: int = 1000,
    ) -> None: ...

    async def move(
        self,
        context: ComputerContext,
        *,
        x: int,
        y: int,
    ) -> None: ...

    async def keypress(
        self,
        context: ComputerContext,
        *,
        keys: List[str],
    ) -> None: ...

    async def drag(
        self,
        context: ComputerContext,
        *,
        path: List[Dict[str, int]],
    ) -> None: ...

    async def get_current_url(self, context: ComputerContext) -> str: ...

    async def goto(
        self,
        context: ComputerContext,
        *,
        url: str,
    ) -> None: ...

    async def back(self, context: ComputerContext) -> None: ...

    async def forward(self, context: ComputerContext) -> None: ...

    async def __aenter__(self, context: ComputerContext) -> "Computer":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> "Computer":
        return self
