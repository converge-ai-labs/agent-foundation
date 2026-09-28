"""Root display history, independent of the model's replaceable context."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from copy import deepcopy
from hashlib import sha256
from typing import Any, Self, cast

from a13n_harness.capabilities.context import CompactionCapability, CompactionSummaryEvent, HandoffCapability
from a13n_harness.context import AgentContext
from a13n_harness.state import (
    AgentContextStateSnapshot,
    CapabilityState,
    HarnessState,
    clone_messages,
    decode_messages,
    encode_messages,
)
from a13n_harness.toolsets.events import HandoffSummaryEvent
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import (
    AgentStreamEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextContent,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext

_STATE_KEY = "a13n.harness-ui.display-history"
_COMPLETED_KEY = "a13n.harness-ui.completed"


class DisplayHistory(BaseModel):
    """Inspection-only messages and their current native-history positions."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    messages_json: bytes | Sequence[ModelMessage] = Field(default=b"[]", alias="messages", exclude=True, repr=False)
    model_positions: tuple[int | None, ...] = ()
    pending_response_position: int | None = None
    model_history_digest: str = ""

    @field_validator("messages_json", mode="before")
    @classmethod
    def _encode_messages(cls, value: Any) -> bytes:
        return encode_messages(value)

    @computed_field
    @property
    def messages(self) -> tuple[ModelMessage, ...]:
        return decode_messages(cast(bytes, self.messages_json))

    @property
    def completed_responses(self) -> tuple[int, ...]:
        return tuple(
            position
            for position, message in enumerate(self.messages)
            if isinstance(message, ModelResponse) and (message.metadata or {}).get(_COMPLETED_KEY) is True
        )

    @model_validator(mode="after")
    def _valid_positions(self) -> Self:
        messages = self.messages
        message_count = len(messages)
        if any(
            position is not None and not 0 <= position < message_count
            for position in (*self.model_positions, self.pending_response_position)
        ):
            raise ValueError("Display history position is outside the saved messages")
        return self


def _message_digest(encoded: bytes) -> str:
    return sha256(json.dumps(json.loads(encoded), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def saved_display_history(state: HarnessState) -> DisplayHistory | None:
    """Read inspection state without changing the continuation's stored schema."""
    return _read_display_history(state, state.agent_context_state.entries.get(_STATE_KEY))


def _read_display_history(state: HarnessState, entry: CapabilityState | None) -> DisplayHistory | None:
    if entry is None:
        return None
    if entry.version != "1":
        raise ValueError("Unsupported display history version")
    display = DisplayHistory.model_validate(entry.data)
    # Older Apps preserve unknown Capability namespaces but do not advance this
    # inspection snapshot. Never apply its positional mapping to different input.
    if display.model_history_digest != _message_digest(cast(bytes, state.message_history_json)):
        return None
    if len(display.model_positions) != len(state.message_history):
        raise ValueError("Display history must map the selected model history")
    return display


def with_display_history(state: HarnessState, display: DisplayHistory) -> HarnessState:
    """Attach UI-owned state in the existing extensible Capability envelope."""
    entries = state.agent_context_state.entries
    entries[_STATE_KEY] = CapabilityState(version="1", data=display.model_dump(mode="json"))
    return state.model_copy(update={"agent_context_state": AgentContextStateSnapshot(entries=entries)})


def detach_display_history(state: HarnessState) -> tuple[HarnessState, DisplayHistory | None]:
    """Restore and remove inspection state with one namespace decode.

    Validate before returning native execution state. Every durable root selection
    must reattach the collector's current history with ``with_display_history``.
    """
    entries = state.agent_context_state.entries
    entry = entries.pop(_STATE_KEY, None)
    display = _read_display_history(state, entry)
    if entry is None:
        return state, display
    runtime = state.model_copy(update={"agent_context_state": AgentContextStateSnapshot(entries=entries)})
    return runtime, display


def _context_boundary(messages: Sequence[ModelMessage]) -> tuple[object, ...]:
    """Compare only owned replacement summaries, never ordinary message timestamps."""
    for message in messages:
        metadata = message.metadata or {}
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and not isinstance(part.content, str):
                    for item in part.content:
                        if isinstance(item, TextContent) and (item.metadata or {}).get("a13n.context") == "handoff":
                            return ("handoff", (item.metadata or {}).get("operation_id"), item.content)
            if metadata.get("a13n.context") == "handoff":
                return ("handoff", message.timestamp)
        if isinstance(message, ModelResponse) and metadata.get("keep") == "compact":
            return (
                "compaction",
                message.timestamp,
                *(part.content for part in message.parts if isinstance(part, TextPart)),
            )
    return ()


class DisplayHistoryCollector(AbstractCapability[AgentContext]):
    """Capture complete root messages before context replacement, not helper Runs."""

    id = _STATE_KEY

    def __init__(self, model_history: Sequence[ModelMessage], saved: DisplayHistory | None = None) -> None:
        # Saved messages are already freshly decoded and detached from their envelope.
        self._messages = list(clone_messages(model_history) if saved is None else saved.messages)
        self._positions: list[int | None] = (
            list(range(len(model_history))) if saved is None else list(saved.model_positions)
        )
        if len(self._positions) != len(model_history):
            raise ValueError("Display history does not match the selected model history")
        self._pending_response_position = saved.pending_response_position if saved is not None else None
        self._boundary = deepcopy(_context_boundary(model_history))
        self._completed_responses = {
            position
            for position, message in enumerate(self._messages)
            if saved is not None
            and isinstance(message, ModelResponse)
            and (message.metadata or {}).get(_COMPLETED_KEY) is True
        }
        self._active_run_id: str | None = None
        self._unmapped_run_id: str | None = None
        self._operations: set[str] = {
            operation
            for message in self._messages
            if isinstance(operation := (message.metadata or {}).get("operation_id"), str)
        }

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost", wraps=(HandoffCapability, CompactionCapability))

    async def wrap_run(self, ctx: RunContext[AgentContext], *, handler: Callable[[], Awaitable[Any]]) -> Any:
        if self._active_run_id is not None:
            return await handler()
        self._active_run_id = ctx.run_id
        self._unmapped_run_id = ctx.run_id
        try:
            return await handler()
        finally:
            self._active_run_id = None

    def _initialize_positions(self, history: Sequence[ModelMessage]) -> None:
        if self._unmapped_run_id is None:
            return
        # Native preparation may merge inherited adjacent requests, including
        # before a failed Run's first model hook. Rebase on the captured history,
        # not wrap_run's initial context, which may still hold the old list.
        inherited = next(
            (index for index, message in enumerate(history) if message.run_id == self._unmapped_run_id),
            len(history),
        )
        suspended_position = None
        if (
            inherited == len(history)
            and self._positions
            and history
            and isinstance(history[-1], ModelResponse)
            and history[-1].state == "suspended"
        ):
            suspended_position = self._positions[-1]
        self._positions = [None] * inherited
        if suspended_position is not None:
            self._positions[-1] = suspended_position
        if inherited < len(history):
            self._pending_response_position = None
        self._boundary = deepcopy(_context_boundary(history))
        self._unmapped_run_id = None

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        if ctx.run_id == self._active_run_id:
            self._collect(ctx.messages)
        return request_context

    async def on_event(self, ctx: RunContext[AgentContext], *, event: AgentStreamEvent) -> None:
        if ctx.run_id != self._active_run_id or not isinstance(event, (HandoffSummaryEvent, CompactionSummaryEvent)):
            return
        self._collect(ctx.messages)
        if event.operation_id in self._operations:
            return
        self._operations.add(event.operation_id)
        kind = "handoff" if isinstance(event, HandoffSummaryEvent) else "compaction"
        metadata = {"a13n.context": kind, "operation_id": event.operation_id}
        self._messages.append(
            ModelRequest(parts=[UserPromptPart([TextContent(event.summary, metadata=metadata)])], metadata=metadata)
            if kind == "handoff"
            else ModelResponse(parts=[TextPart(event.summary)], metadata={**metadata, "keep": "compact"})
        )

    def capture(self, history: Sequence[ModelMessage], *, completed: bool = False) -> DisplayHistory:
        self._collect(history, completed=completed)
        return DisplayHistory(
            messages=tuple(self._messages),
            model_positions=tuple(self._positions),
            pending_response_position=self._pending_response_position,
            model_history_digest=_message_digest(encode_messages(history)),
        )

    def _collect(self, history: Sequence[ModelMessage], *, completed: bool = False) -> None:
        """Advance inspection copies without serializing an unused checkpoint."""
        self._initialize_positions(history)
        boundary = _context_boundary(history)
        if boundary != self._boundary:
            # Before-hooks have already captured the original messages. The new
            # prefix contains only synthetic context and retained input replays.
            # Neither replaces nor repeats the original display rows.
            self._positions = []
            self._pending_response_position = None
            self._boundary = deepcopy(boundary)
            for message in history:
                metadata = message.metadata or {}
                if (
                    metadata.get("a13n.context") in ("handoff", "compaction")
                    or metadata.get("keep") == "compact"
                    or "a13n.steering-run" in metadata
                ):
                    self._positions.append(None)
                else:
                    self._positions.append(len(self._messages))
                    self._messages.append(deepcopy(message))
        else:
            if len(history) < len(self._positions):
                # Native suspended-response preparation temporarily removes the
                # tail before requesting its completion. Keep its display slot,
                # including across a checkpoint/reload, for the merged response.
                position = self._positions[-1]
                if (
                    len(history) != len(self._positions) - 1
                    or position is None
                    or not isinstance(response := self._messages[position], ModelResponse)
                    or response.state != "suspended"
                ):
                    raise ValueError("Model history changed without an owned context replacement")
                self._pending_response_position = position
                self._positions.pop()
            for index, message in enumerate(history):
                if index >= len(self._positions):
                    if self._pending_response_position is not None and isinstance(message, ModelResponse):
                        position = self._pending_response_position
                        self._positions.append(position)
                        self._messages[position] = deepcopy(message)
                        self._pending_response_position = None
                    else:
                        self._positions.append(len(self._messages))
                        self._messages.append(deepcopy(message))
                elif (position := self._positions[index]) is not None:
                    self._messages[position] = deepcopy(message)
        if (
            completed
            and history
            and isinstance(history[-1], ModelResponse)
            and history[-1].state == "complete"
            and (history[-1].metadata or {}).get("keep") != "compact"
        ):
            position = self._positions[-1]
            if position is not None and any(isinstance(part, TextPart) for part in history[-1].parts):
                self._completed_responses.add(position)
        # Mark only inspection copies. Native model context and the version-1
        # display envelope stay unchanged, including for older App readers.
        for position in self._completed_responses:
            message = self._messages[position]
            message.metadata = {**(message.metadata or {}), _COMPLETED_KEY: True}
