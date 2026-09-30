"""Producer-side native history reconciliation for compact display capture."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from hashlib import sha256
from typing import Any

from a13n_harness.capabilities.context import CompactionCapability, CompactionSummaryEvent, HandoffCapability
from a13n_harness.context import AgentContext
from a13n_harness.state import encode_messages
from a13n_harness.toolsets.events import HandoffSummaryEvent
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, ValidatedToolArgs, WrapToolExecuteHandler
from pydantic_ai.messages import (
    AgentStreamEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    OutputToolResultEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
    TextContent,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import DeferredToolRequests, ToolDefinition

from a13n_stream_protocol.display import DisplayScope, DisplaySnapshot
from a13n_stream_protocol.projector import DisplayProjector


class HistoryCursor(BaseModel):
    """Positional continuity only; never a second native message transcript."""

    model_config = ConfigDict(extra="forbid")
    positions: list[int | None] = Field(default_factory=list)
    owners: dict[int, str] = Field(default_factory=dict)
    digests: dict[int, str] = Field(default_factory=dict)
    tool_scopes: dict[str, str] = Field(default_factory=dict)
    next_index: int = 0
    pending_response: int | None = None
    suspended_response: int | None = None
    boundary: str = ""


def context_boundary(history: Sequence[ModelMessage]) -> str:
    for message in history:
        metadata = message.metadata or {}
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and not isinstance(part.content, str):
                    for item in part.content:
                        if isinstance(item, TextContent) and (item.metadata or {}).get("a13n.context") == "handoff":
                            return f"handoff:{(item.metadata or {}).get('operation_id')}"
            if metadata.get("a13n.context") == "handoff":
                return f"handoff:{message.timestamp}"
        if isinstance(message, ModelResponse) and metadata.get("keep") == "compact":
            return f"compaction:{message.timestamp.isoformat()}"
    return ""


def _synthetic(message: ModelMessage) -> bool:
    metadata = message.metadata or {}
    return bool(metadata.get("a13n.context") or metadata.get("keep") == "compact" or "a13n.steering-run" in metadata)


class DisplaySession(AbstractCapability[AgentContext]):
    """One logical producer's hooks, sharing its root projector with inline Runs.

    Native on_event updates provisional display. capture() reconciles the exact
    canonical history supplied by the checkpoint producer; it never waits for
    public stream delivery. A helper model using the same context is excluded.
    """

    id = "a13n.display-capture"

    def __init__(
        self,
        projector: DisplayProjector,
        history: Sequence[ModelMessage] = (),
        *,
        thread_id: str,
        include_initial: bool = True,
    ) -> None:
        self.projector = projector
        self.thread_id = thread_id
        stored = projector.state.continuity.get(thread_id)
        self.cursor = HistoryCursor.model_validate(stored) if stored is not None else HistoryCursor()
        self._initial_history = history
        self._include_initial = include_initial
        self._native_run: str | None = None
        self._history_run: str | None = None
        self._scope: str | None = None
        self._unmapped = True
        self._request_slots: dict[int, int] = {}
        self._part_offsets: dict[int, int] = {}
        self._step = 0
        self._prepared = False

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost", wraps=(HandoffCapability, CompactionCapability))

    async def wrap_run(self, ctx: RunContext[AgentContext], *, handler: Callable[[], Awaitable[Any]]) -> Any:
        if self._native_run is not None:
            return await handler()
        self._native_run = ctx.run_id
        self._history_run = ctx.run_id
        self._scope = ctx.deps.run_id
        existing = self.projector.state.scopes.get(self._scope)
        if existing is None:
            self.projector.scope(DisplayScope(id=self._scope, thread_id=ctx.deps.thread_id, run_id=ctx.deps.run_id))
        if not self.cursor.owners and self._include_initial:
            self._collect(self._initial_history)
        elif not self._include_initial:
            # Imported/parent history supplies model context, not this producer's output.
            self.cursor.positions = [None] * len(self._initial_history)
            self.cursor.boundary = context_boundary(self._initial_history)
        self._initial_history = ()
        try:
            result = await handler()
            self._collect(result.all_messages())
            status = "completed"
            if isinstance(result.output, DeferredToolRequests):
                status = "deferred"
                for call in (*result.output.calls, *result.output.approvals):
                    self.projector.tool_status(
                        self.cursor.tool_scopes.get(call.tool_call_id, self._scope), call.tool_call_id, "deferred"
                    )
            self.projector.finish_scope(self._scope, status)
            return result
        except asyncio.CancelledError:
            self.projector.finish_scope(self._scope, "cancelled")
            raise
        except Exception:
            self.projector.finish_scope(self._scope, "failed")
            raise
        finally:
            self._native_run = None

    def _reserve(self) -> int:
        assert self._scope is not None
        position = self.cursor.next_index
        self.cursor.next_index += 1
        self.cursor.owners[position] = self._scope
        return position

    def _prepare(self, history: Sequence[ModelMessage]) -> None:
        if not self._unmapped or self._history_run is None:
            return
        inherited = next((i for i, message in enumerate(history) if message.run_id == self._history_run), len(history))
        suspended = (
            self.cursor.suspended_response
            if history and isinstance(history[-1], ModelResponse) and history[-1].state == "suspended"
            else None
        )
        self.cursor.positions = [None] * inherited
        if suspended is not None and self.cursor.positions:
            self.cursor.positions[-1] = suspended
        if inherited < len(history):
            self.cursor.pending_response = None
        self.cursor.boundary = context_boundary(history)
        self._unmapped = False

    def _collect(self, history: Sequence[ModelMessage]) -> None:
        if self._scope is None:
            raise ValueError("Display session has not entered a Run")
        if self._prepared:
            self._prepare(history)
        boundary = context_boundary(history)
        if boundary != self.cursor.boundary:
            self.cursor.positions = []
            self.cursor.pending_response = None
            self.cursor.suspended_response = None
            self.cursor.boundary = boundary
            self._request_slots.clear()
            replacement = True
        else:
            replacement = False
        if len(history) < len(self.cursor.positions):
            if (
                len(history) != len(self.cursor.positions) - 1
                or self.cursor.positions[-1] != self.cursor.suspended_response
            ):
                raise ValueError("Native history changed without a context replacement")
            self.cursor.pending_response = self.cursor.positions.pop()
        for index, message in enumerate(history):
            if index >= len(self.cursor.positions):
                if replacement and _synthetic(message):
                    self.cursor.positions.append(None)
                    continue
                if isinstance(message, ModelResponse):
                    position = self.cursor.pending_response
                    if position is None:
                        position = self._request_slots.get(self._step)
                    if position is None:
                        position = self._reserve()
                    self.cursor.pending_response = None
                else:
                    position = self._reserve()
                self.cursor.positions.append(position)
            position = self.cursor.positions[index]
            if position is None:
                continue
            digest = sha256(encode_messages([message])).hexdigest()
            if self.cursor.digests.get(position) != digest:
                scope = self.cursor.owners[position]
                for part in message.parts:
                    if isinstance(part, ToolCallPart):
                        self.cursor.tool_scopes[part.tool_call_id] = scope
                self.projector.reconcile_message(scope, position, message, tool_scopes=self.cursor.tool_scopes)
                self.cursor.digests[position] = digest
            if isinstance(message, ModelResponse):
                self.cursor.suspended_response = position if message.state == "suspended" else None
        calls = {
            part.tool_call_id
            for message in history
            for part in message.parts
            if isinstance(part, ToolCallPart | ToolReturnPart | RetryPromptPart)
        }
        self.cursor.tool_scopes = {call: scope for call, scope in self.cursor.tool_scopes.items() if call in calls}
        self._persist_cursor()

    def _persist_cursor(self) -> None:
        active = {position for position in self.cursor.positions if position is not None}
        active.update(self._request_slots.values())
        active.update(
            position
            for position in (self.cursor.pending_response, self.cursor.suspended_response)
            if position is not None
        )
        self.cursor.owners = {position: scope for position, scope in self.cursor.owners.items() if position in active}
        self.cursor.digests = {
            position: digest for position, digest in self.cursor.digests.items() if position in active
        }
        self.projector.state.continuity[self.thread_id] = self.cursor.model_dump(mode="json")

    def capture(self, history: Sequence[ModelMessage]) -> DisplaySnapshot:
        # Preparation can merge inherited requests then fail in instructions,
        # before before_model_request. Map the exported canonical history,
        # not wrap_run's stale pre-preparation list, even after native exit.
        self._prepare(history)
        self._collect(history)
        # The safe boundary may follow compaction after before_model_request.
        if self.cursor.pending_response is not None:
            self._request_slots[self._step] = self.cursor.pending_response
        elif self._step not in self._request_slots:
            self._request_slots[self._step] = self._reserve()
        self._persist_cursor()
        return self.projector.capture()

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        if ctx.run_id == self._native_run:
            self._step = ctx.run_step
            self._prepared = True
            self._collect(ctx.messages)
            continuing = self.cursor.pending_response
            if (
                continuing is None
                and ctx.messages
                and isinstance(ctx.messages[-1], ModelResponse)
                and ctx.messages[-1].state == "suspended"
            ):
                continuing = self.cursor.suspended_response
            self._request_slots = {ctx.run_step: continuing if continuing is not None else self._reserve()}
            self._part_offsets = {
                ctx.run_step: (
                    len(ctx.messages[-1].parts)
                    if continuing is not None and ctx.messages and isinstance(ctx.messages[-1], ModelResponse)
                    else 0
                )
            }
            self._persist_cursor()
        return request_context

    async def wrap_tool_execute(
        self,
        ctx: RunContext[AgentContext],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        if ctx.run_id != self._native_run or self._scope is None:
            return await handler(args)
        self._collect(ctx.messages)
        scope = self.cursor.tool_scopes.get(call.tool_call_id, self._scope)
        self.projector.tool_status(scope, call.tool_call_id, "running")
        try:
            return await handler(args)
        except asyncio.CancelledError:
            self.projector.tool_status(scope, call.tool_call_id, "cancelled")
            raise
        except Exception:
            self.projector.tool_status(scope, call.tool_call_id, "failed")
            raise

    async def on_event(self, ctx: RunContext[AgentContext], *, event: AgentStreamEvent) -> None:
        if ctx.run_id != self._native_run or self._scope is None:
            return
        if isinstance(event, HandoffSummaryEvent | CompactionSummaryEvent):
            self._collect(ctx.messages)
            self.projector.summary(
                self._scope,
                event.operation_id,
                "handoff" if isinstance(event, HandoffSummaryEvent) else "compaction",
                event.summary,
                files=event.files if isinstance(event, HandoffSummaryEvent) else (),
            )
            return
        if isinstance(event, PartStartEvent | PartDeltaEvent | PartEndEvent):
            position = self._request_slots.get(ctx.run_step)
            if position is None:
                raise ValueError("Native response has no prepared display slot")
            event = replace(event, index=event.index + self._part_offsets.get(ctx.run_step, 0))
        else:
            position = self._request_slots.get(ctx.run_step, self.cursor.next_index)
        scope = self.cursor.owners.get(position, self._scope)
        if isinstance(event, FunctionToolResultEvent | OutputToolResultEvent):
            scope = self.cursor.tool_scopes.get(event.part.tool_call_id, scope)
        self.projector.observe(scope, position, event)


class DisplayCapture(AbstractCapability[AgentContext]):
    """Definition-side factory for independent native sessions on one projector.

    Install the same factory on root and inline definitions. Independent async
    producers instead install a factory with their own projector and coverage.
    """

    id = DisplaySession.id

    def __init__(self, projector: DisplayProjector, *, include_initial: bool = True) -> None:
        self.projector = projector
        self.include_initial = include_initial
        self.sessions: dict[str, DisplaySession] = {}

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        session = self.sessions.get(ctx.deps.run_id)
        if session is None:
            session = DisplaySession(
                self.projector,
                ctx.messages,
                thread_id=ctx.deps.thread_id,
                include_initial=self.include_initial,
            )
            self.sessions[ctx.deps.run_id] = session
        return session

    def capture(self, run_id: str, history: Sequence[ModelMessage]) -> DisplaySnapshot:
        return self.sessions[run_id].capture(history)
