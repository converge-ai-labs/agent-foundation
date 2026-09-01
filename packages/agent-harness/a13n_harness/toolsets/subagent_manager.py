"""Portable parent-Agent state and tools for process-local asynchronous subagents."""

from __future__ import annotations

import asyncio
import re
from collections import OrderedDict
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from types import MappingProxyType
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, field_serializer, field_validator
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import RunUsage, UsageLimits

from a13n_harness.capabilities.subagents import (
    SubagentActivitySnapshot,
    SubagentBackendEventHook,
    SubagentExecutionSnapshot,
    SubagentOperator,
)
from a13n_harness.context import AgentContext, SubagentCollection
from a13n_harness.errors import StateError
from a13n_harness.state import AgentContextState
from a13n_harness.tools.metadata import (
    MAX_OUTPUT_BYTES,
    HarnessTool,
    HarnessToolMetadata,
    IdempotencySemantics,
    ToolEffect,
    ToolOutputPolicy,
)

_SUBAGENT_STATE_ID = "a13n.subagent-manager"
_SUBAGENT_STATE_VERSION = "1"
_SUBAGENT_ID_PATTERN = re.compile(r"^subagent-([1-9][0-9]*)$")
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "lost"})
_MAX_REFERENCE_ENTRIES = 100_000
_DEFAULT_PAGE_SIZE = 20
_MAX_PAGE_SIZE = 100
_JSON_ADAPTER = TypeAdapter(JsonValue)
_STATUS_OUTPUT_POLICY = ToolOutputPolicy(
    max_inline_bytes=256 * 1024,
    max_output_bytes=4 * 1024 * 1024,
    overflow="truncate",
    redact=True,
)
_WAIT_OUTPUT_POLICY = ToolOutputPolicy(
    max_inline_bytes=256 * 1024,
    max_output_bytes=MAX_OUTPUT_BYTES,
    overflow="spill",
    redact=True,
)

SubagentStatus = Literal["running", "succeeded", "failed", "cancelled", "lost"]


class ManagedSubagentState(BaseModel):
    """Portable parent-private projection of one process-local child execution."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    subagent_id: str = Field(pattern=r"^subagent-[1-9][0-9]*$", max_length=24)
    subagent_name: str = Field(min_length=1, max_length=63)
    child_definition_id: str = Field(min_length=1, max_length=256)
    backend_id: str = Field(min_length=1, max_length=512)
    prompt: str = Field(min_length=1)
    status: SubagentStatus
    resumed_from: str | None = Field(default=None, pattern=r"^subagent-[1-9][0-9]*$", max_length=24)
    failure: JsonValue | None = None
    resumable: bool = False
    thread_id: str | None = Field(default=None, min_length=1, max_length=256)


class SubagentManagerState(BaseModel):
    """Versioned subagent-N mapping stored in the current parent Agent state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_thread_id: str = Field(min_length=1, max_length=256)
    next_sequence: int = Field(default=1, ge=1, le=_MAX_REFERENCE_ENTRIES + 1)
    subagents: Mapping[str, ManagedSubagentState] = Field(default_factory=dict)

    @field_validator("subagents", mode="after")
    @classmethod
    def _validate_subagents(
        cls,
        value: Mapping[str, ManagedSubagentState],
    ) -> Mapping[str, ManagedSubagentState]:
        copied = dict(value)
        if len(copied) > _MAX_REFERENCE_ENTRIES:
            raise ValueError("subagent state exceeds the finite reference limit")
        if any(_SUBAGENT_ID_PATTERN.fullmatch(key) is None or key != item.subagent_id for key, item in copied.items()):
            raise ValueError("subagent state contains an invalid compact reference")
        return MappingProxyType(copied)

    @field_serializer("subagents")
    def _serialize_subagents(
        self,
        value: Mapping[str, ManagedSubagentState],
    ) -> dict[str, ManagedSubagentState]:
        return dict(value)


@dataclass(slots=True)
class _SubagentEntry:
    state: ManagedSubagentState
    output: JsonValue | None = None
    usage: RunUsage = dataclass_field(default_factory=RunUsage)


class _AsyncSubagentProjection:
    """Manage one parent Run's portable projection over a stable operator."""

    def __init__(
        self,
        *,
        operator: SubagentOperator,
        children: SubagentCollection,
    ) -> None:
        if not isinstance(operator, SubagentOperator):
            raise TypeError("operator must be a SubagentOperator")
        self._operator = operator
        self._children = children
        self._entries: OrderedDict[str, _SubagentEntry] = OrderedDict()
        self._next_sequence = 1
        self._state_store: AgentContextState | None = None
        self._owner_thread_id: str | None = None
        self._active_context: RunContext[AgentContext] | None = None
        self._state_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._loaded = False
        self._observers: dict[str, SubagentBackendEventHook] = {}

    @asynccontextmanager
    async def active_run(self, ctx: RunContext[AgentContext]) -> AsyncGenerator[None]:
        """Bind parent state, reconcile retained work, and enable active-run hints."""
        await self._load(ctx.deps.state, owner_thread_id=ctx.deps.thread_id)
        owned = self._active_context is None
        if owned:
            self._active_context = ctx
            await self._attach_nonterminal()
        try:
            yield
        finally:
            if owned and self._active_context is ctx:
                self._active_context = None

    async def delegate(
        self,
        *,
        subagent_name: str,
        prompt: str,
        resume_from_backend_id: str | None = None,
        resumed_from: str | None = None,
    ) -> dict[str, JsonValue]:
        """Start one exact declared child and return after backend acceptance."""
        self._require_loaded()
        try:
            child = self._children.require(subagent_name)
        except KeyError as exc:
            raise ToolFailed("Unknown managed subagent.") from exc
        async with self._start_lock:
            async with self._state_lock:
                if self._next_sequence > _MAX_REFERENCE_ENTRIES:
                    raise ToolFailed("Managed subagent reference capacity is exhausted.")
                subagent_id = f"subagent-{self._next_sequence}"
            limits = _intersect_usage_limits(
                child.executable._fresh_definition_usage_limits(),
                child.declaration.usage_limits,
                self._operator.usage_limits,
            )
            observer = self._event_callback(subagent_id)
            self._observers[subagent_id] = observer
            snapshot: SubagentExecutionSnapshot | None = None
            try:
                snapshot = await self._operator.start(
                    self._context(),
                    child,
                    prompt,
                    subagent_id,
                    resume_from_backend_id,
                    limits,
                    observer,
                )
                _validate_snapshot_identity(snapshot, snapshot.backend_id)
                accepted_status = cast(SubagentStatus, snapshot.status)
                entry = _SubagentEntry(
                    state=ManagedSubagentState(
                        subagent_id=subagent_id,
                        subagent_name=subagent_name,
                        child_definition_id=child.definition.definition_id,
                        backend_id=snapshot.backend_id,
                        prompt=prompt,
                        status=cast(SubagentStatus, snapshot.status),
                        resumed_from=resumed_from,
                        failure=_detach_json(snapshot.failure),
                        resumable=snapshot.resumable,
                        thread_id=snapshot.thread_id,
                    ),
                    output=_detach_json(snapshot.output),
                    usage=deepcopy(snapshot.usage),
                )
                async with self._state_lock:
                    self._entries[subagent_id] = entry
                    self._next_sequence += 1
                    try:
                        await self._persist_locked()
                    except BaseException:
                        self._entries.pop(subagent_id, None)
                        self._next_sequence -= 1
                        raise
            except BaseException:
                self._observers.pop(subagent_id, None)
                if snapshot is not None:
                    await _cancel_accepted_subagent(self._operator, self._context(), snapshot.backend_id)
                raise
        return {"execution_id": subagent_id, "status": accepted_status}

    async def info(
        self,
        execution_id: str | None,
        *,
        offset: int,
        limit: int,
    ) -> JsonValue:
        """Inspect one execution or list bounded status snapshots."""
        self._require_loaded()
        if execution_id is not None:
            entry = self._entry(execution_id)
            await self._refresh_entry(entry)
            activity = await self._operator.activity(self._context(), entry.state.backend_id)
            if activity is not None and not isinstance(activity, SubagentActivitySnapshot):
                raise TypeError("subagent backend returned an invalid activity snapshot")
            return {
                **self._project(entry),
                "input": entry.state.prompt,
                "activity": None if activity is None else activity.model_dump(mode="json"),
            }
        entries = tuple(self._entries.values())
        selected = entries[offset : offset + limit]
        for entry in selected:
            await self._refresh_entry(entry)
        return {
            "available_subagents": [
                {"name": child.declaration.name, "description": child.declaration.description[:512]}
                for child in self._children.values()
            ],
            "executions": [self._project(entry) for entry in selected],
            "offset": offset,
            "showing": len(selected),
            "total": len(entries),
            "next_offset": offset + len(selected) if offset + len(selected) < len(entries) else None,
        }

    async def wait(
        self,
        execution_id: str | None,
        *,
        timeout_seconds: float | None,
        offset: int,
        limit: int,
    ) -> JsonValue:
        """Wait once for one execution or fan in current nonterminal executions."""
        self._require_loaded()
        if execution_id is not None:
            entry = self._entry(execution_id)
            await self._wait_entry(entry, timeout_seconds)
            return {**self._project(entry), "output": _detach_json(entry.output)}
        candidates = tuple(entry for entry in self._entries.values() if entry.state.status not in _TERMINAL_STATUSES)
        await asyncio.gather(*(self._wait_entry(entry, timeout_seconds) for entry in candidates))
        entries = tuple(self._entries.values())[offset : offset + limit]
        return {
            "executions": [self._project(entry) for entry in entries],
            "offset": offset,
            "showing": len(entries),
            "total": len(self._entries),
            "next_offset": offset + len(entries) if offset + len(entries) < len(self._entries) else None,
        }

    async def steer(self, execution_id: str, message: str) -> dict[str, JsonValue]:
        """Send one steering input to a currently active child."""
        entry = self._entry(execution_id)
        if entry.state.status != "running":
            raise ToolFailed("The managed subagent is not running.")
        enqueue_id = await self._operator.steer(self._context(), entry.state.backend_id, message)
        return {"execution_id": execution_id, "enqueue_id": enqueue_id}

    async def cancel(self, execution_id: str) -> dict[str, JsonValue]:
        """Request cancellation without inventing a terminal result."""
        entry = self._entry(execution_id)
        if entry.state.status != "running":
            return {"execution_id": execution_id, "accepted": False}
        accepted = await self._operator.cancel(self._context(), entry.state.backend_id)
        if accepted:
            await self._refresh_entry(entry)
        return {"execution_id": execution_id, "accepted": accepted}

    async def resume(self, execution_id: str, prompt: str) -> dict[str, JsonValue]:
        """Start a linked execution from one collected terminal child state."""
        entry = self._entry(execution_id)
        await self._refresh_entry(entry)
        if entry.state.status not in _TERMINAL_STATUSES:
            raise ToolFailed("The managed subagent is still running.")
        if not entry.state.resumable:
            raise ToolFailed("The managed subagent backend does not allow this execution to resume.")
        try:
            child = self._children.require(entry.state.subagent_name)
        except KeyError as exc:
            raise ToolFailed("The retained managed subagent is unavailable.") from exc
        if child.definition.definition_id != entry.state.child_definition_id:
            raise ToolFailed("The retained managed subagent definition is incompatible.")
        return await self.delegate(
            subagent_name=entry.state.subagent_name,
            prompt=prompt,
            resume_from_backend_id=entry.state.backend_id,
            resumed_from=execution_id,
        )

    async def _load(self, store: AgentContextState, *, owner_thread_id: str) -> None:
        if self._loaded:
            if self._state_store is not store or self._owner_thread_id != owner_thread_id:
                raise RuntimeError("Subagent run projection cannot cross Agent Contexts")
            return
        restored = await store.read(
            _SUBAGENT_STATE_ID,
            SubagentManagerState,
            version=_SUBAGENT_STATE_VERSION,
        )
        if restored is None:
            state = SubagentManagerState(owner_thread_id=owner_thread_id)
        elif restored.owner_thread_id != owner_thread_id:
            state = SubagentManagerState(
                owner_thread_id=owner_thread_id,
                next_sequence=restored.next_sequence,
            )
        else:
            state = restored
        self._entries = OrderedDict(
            (subagent_id, _SubagentEntry(value))
            for subagent_id, value in sorted(state.subagents.items(), key=lambda item: _sequence(item[0]))
        )
        highest = max((_sequence(subagent_id) for subagent_id in self._entries), default=0)
        self._next_sequence = max(state.next_sequence, highest + 1)
        self._state_store = store
        self._owner_thread_id = owner_thread_id
        self._loaded = True
        if restored is not None and restored.owner_thread_id != owner_thread_id:
            await self._persist_locked()

    async def _attach_nonterminal(self) -> None:
        for entry in tuple(self._entries.values()):
            if entry.state.status in _TERMINAL_STATUSES:
                continue
            child = self._children.get(entry.state.subagent_name)
            if child is None or child.definition.definition_id != entry.state.child_definition_id:
                await self._mark_lost(entry)
                continue
            try:
                observer = self._event_callback(entry.state.subagent_id)
                self._observers[entry.state.subagent_id] = observer
                snapshot = await self._operator.attach(
                    self._context(),
                    entry.state.backend_id,
                    observer,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._notify(entry, "gap")
                continue
            if snapshot is None:
                await self._mark_lost(entry)
            else:
                await self._apply_snapshot(entry, snapshot)

    async def _refresh_entry(self, entry: _SubagentEntry) -> None:
        snapshot = await self._operator.snapshot(self._context(), entry.state.backend_id)
        if snapshot is None:
            await self._mark_lost(entry)
        else:
            await self._apply_snapshot(entry, snapshot)

    async def _wait_entry(self, entry: _SubagentEntry, timeout_seconds: float | None) -> None:
        snapshot = await self._operator.wait(self._context(), entry.state.backend_id, timeout_seconds)
        if snapshot is None:
            await self._mark_lost(entry)
        else:
            await self._apply_snapshot(entry, snapshot)

    def _event_callback(self, subagent_id: str) -> Callable[[SubagentExecutionSnapshot], Awaitable[None]]:
        async def callback(snapshot: SubagentExecutionSnapshot) -> None:
            entry = self._entries.get(subagent_id)
            if entry is None:
                return
            if self._active_context is not None:
                await self._apply_snapshot(entry, snapshot)

        return callback

    async def _apply_snapshot(self, entry: _SubagentEntry, snapshot: SubagentExecutionSnapshot) -> None:
        _validate_snapshot_identity(snapshot, entry.state.backend_id)
        async with self._state_lock:
            previous = entry.state.status
            if previous in _TERMINAL_STATUSES:
                return
            updated = entry.state.model_copy(
                update={
                    "status": snapshot.status,
                    "failure": _detach_json(snapshot.failure),
                    "resumable": snapshot.resumable,
                    "thread_id": snapshot.thread_id,
                }
            )
            entry.state = updated
            entry.output = _detach_json(snapshot.output)
            entry.usage = deepcopy(snapshot.usage)
            await self._persist_locked()
        if updated.status in _TERMINAL_STATUSES:
            await self._notify(entry, "completion")

    async def _mark_lost(self, entry: _SubagentEntry) -> None:
        async with self._state_lock:
            if entry.state.status in _TERMINAL_STATUSES:
                return
            entry.state = entry.state.model_copy(
                update={
                    "status": "lost",
                    "failure": {"code": "subagent_backend_lost"},
                }
            )
            entry.output = None
            await self._persist_locked()
        await self._notify(entry, "gap")

    async def _persist_locked(self) -> None:
        store = self._state_store
        if store is None:
            raise RuntimeError("SubagentManager is not bound to AgentContext State")
        owner_thread_id = self._owner_thread_id
        if owner_thread_id is None:
            raise RuntimeError("Subagent run projection has no owner Thread")
        state = SubagentManagerState(
            owner_thread_id=owner_thread_id,
            next_sequence=self._next_sequence,
            subagents={subagent_id: entry.state for subagent_id, entry in self._entries.items()},
        )
        await store.write(_SUBAGENT_STATE_ID, state, version=_SUBAGENT_STATE_VERSION)

    def _entry(self, execution_id: str) -> _SubagentEntry:
        self._require_loaded()
        if _SUBAGENT_ID_PATTERN.fullmatch(execution_id) is None:
            raise ToolFailed("Expected a managed subagent execution ID.")
        entry = self._entries.get(execution_id)
        if entry is None:
            raise ToolFailed("Managed subagent execution ID is unknown.")
        return entry

    async def _notify(self, entry: _SubagentEntry, kind: Literal["completion", "gap"]) -> None:
        active = self._active_context
        if active is None:
            return
        subagent_id = entry.state.subagent_id
        if kind == "completion":
            message = (
                f"Background subagent {subagent_id} has finished. "
                f"Call wait_subagent with execution_id={subagent_id!r} to collect its result."
            )
        else:
            message = (
                f"Background subagent {subagent_id} can no longer be observed by this backend. "
                "Call subagent_info to inspect its retained status."
            )
        await active.deps._steering.notify(
            message,
            source="async_subagent",
            references=(subagent_id,),
        )

    def _project(self, entry: _SubagentEntry) -> dict[str, JsonValue]:
        state = entry.state
        result: dict[str, JsonValue] = {
            "execution_id": state.subagent_id,
            "subagent": state.subagent_name,
            "status": state.status,
            "resumed_from": state.resumed_from,
            "failure": state.failure,
            "resumable": state.resumable,
            "thread_id": state.thread_id,
            "usage": TypeAdapter(RunUsage).dump_python(entry.usage, mode="json"),
        }
        return result

    def require_context(self, ctx: RunContext[AgentContext]) -> None:
        """Reject a Tool invocation from any other logical Run."""
        active = self._active_context
        if active is None or ctx.deps is not active.deps:
            raise RuntimeError("Subagent projection cannot cross logical Runs")

    def _context(self) -> AgentContext:
        active = self._active_context
        if active is None:
            raise RuntimeError("Subagent operation requires an active Harness Run")
        return active.deps

    def _require_loaded(self) -> None:
        if not self._loaded:
            raise RuntimeError("Subagent run projection must be entered through its Capability wrap_run")


class SubagentManagerToolset:
    """Six standard model-facing operations over one async projection."""

    def __init__(
        self,
        projection: _AsyncSubagentProjection | Callable[[], _AsyncSubagentProjection],
        *,
        children: SubagentCollection,
    ) -> None:
        self._projection_factory = projection if callable(projection) else lambda: projection
        self._children = children

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        available = "; ".join(
            f"{child.declaration.name}: {child.declaration.description[:512]}" for child in self._children.values()
        )
        instructions = (
            "Managed subagents run asynchronously in the current Host process. delegate returns immediately. "
            "Call subagent_info without an execution_id to list status summaries, or with one ID to inspect its "
            "bounded input and recent activity. Use wait_subagent to collect a complete result. Available "
            "subagents: " + (available or "none")
        )
        return FunctionToolset(
            [
                self._tool(self.delegate, "subagent.delegate", {"execute"}, "none"),
                self._tool(self.subagent_info, "subagent.info", {"read"}, "read_only"),
                self._tool(
                    self.wait_subagent,
                    "subagent.wait",
                    {"read"},
                    "read_only",
                    output_policy=_WAIT_OUTPUT_POLICY,
                ),
                self._tool(
                    self.steer_subagent,
                    "subagent.steer",
                    {"external_communication"},
                    "none",
                ),
                self._tool(self.cancel_subagent, "subagent.cancel", {"execute"}, "none"),
                self._tool(self.resume_subagent, "subagent.resume", {"execute"}, "none"),
            ],
            id="a13n-subagent-manager-tools",
            instructions=instructions,
        )

    async def delegate(
        self,
        ctx: RunContext[AgentContext],
        subagent_name: Annotated[
            str,
            Field(
                description="Exact declared subagent name",
                pattern=r"^[a-z][a-z0-9_-]{0,62}$",
                max_length=63,
            ),
        ],
        prompt: Annotated[
            str,
            Field(description="Bounded task, constraints, and expected result", min_length=1),
        ],
    ) -> JsonValue:
        """Start one declared subagent and return after backend acceptance."""
        return await self._projection(ctx).delegate(subagent_name=subagent_name, prompt=prompt)

    async def subagent_info(
        self,
        ctx: RunContext[AgentContext],
        execution_id: Annotated[
            str | None,
            Field(
                description="Execution ID for bounded detail; omit to list status summaries",
                pattern=r"^subagent-[1-9][0-9]*$",
                max_length=24,
            ),
        ] = None,
        execution_offset: Annotated[
            int,
            Field(description="Zero-based list offset; used only when execution_id is omitted", ge=0),
        ] = 0,
        execution_limit: Annotated[
            int,
            Field(
                description="Maximum list entries; used only when execution_id is omitted",
                ge=1,
                le=_MAX_PAGE_SIZE,
            ),
        ] = _DEFAULT_PAGE_SIZE,
    ) -> JsonValue:
        """List status summaries or inspect one execution's bounded input and activity."""
        return await self._projection(ctx).info(
            execution_id,
            offset=execution_offset,
            limit=execution_limit,
        )

    async def wait_subagent(
        self,
        ctx: RunContext[AgentContext],
        execution_id: Annotated[
            str | None,
            Field(
                description="Execution ID for a complete result; omit to fan in current executions",
                pattern=r"^subagent-[1-9][0-9]*$",
                max_length=24,
            ),
        ] = None,
        timeout_seconds: Annotated[
            float | None,
            Field(
                description="Maximum seconds to wait once; omit for no timeout",
                gt=0,
                le=300,
                allow_inf_nan=False,
            ),
        ] = None,
        execution_offset: Annotated[
            int,
            Field(description="Zero-based fan-in result offset; used only when execution_id is omitted", ge=0),
        ] = 0,
        execution_limit: Annotated[
            int,
            Field(
                description="Maximum fan-in summaries; used only when execution_id is omitted",
                ge=1,
                le=_MAX_PAGE_SIZE,
            ),
        ] = _DEFAULT_PAGE_SIZE,
    ) -> JsonValue:
        """Wait once for one complete result or a summary-only fan-in snapshot."""
        return await self._projection(ctx).wait(
            execution_id,
            timeout_seconds=timeout_seconds,
            offset=execution_offset,
            limit=execution_limit,
        )

    async def steer_subagent(
        self,
        ctx: RunContext[AgentContext],
        execution_id: Annotated[
            str,
            Field(description="Running execution ID", pattern=r"^subagent-[1-9][0-9]*$", max_length=24),
        ],
        message: Annotated[str, Field(description="New instruction for the live subagent", min_length=1)],
    ) -> JsonValue:
        """Offer one steering message to a running execution."""
        return await self._projection(ctx).steer(execution_id, message)

    async def cancel_subagent(
        self,
        ctx: RunContext[AgentContext],
        execution_id: Annotated[
            str,
            Field(description="Running execution ID", pattern=r"^subagent-[1-9][0-9]*$", max_length=24),
        ],
    ) -> JsonValue:
        """Request cancellation without inventing a terminal state."""
        return await self._projection(ctx).cancel(execution_id)

    async def resume_subagent(
        self,
        ctx: RunContext[AgentContext],
        execution_id: Annotated[
            str,
            Field(description="Terminal resumable execution ID", pattern=r"^subagent-[1-9][0-9]*$", max_length=24),
        ],
        prompt: Annotated[
            str,
            Field(description="Next bounded task, constraints, and expected result", min_length=1),
        ],
    ) -> JsonValue:
        """Start a linked continuation from one resumable terminal execution."""
        return await self._projection(ctx).resume(execution_id, prompt)

    def _projection(self, ctx: RunContext[AgentContext]) -> _AsyncSubagentProjection:
        projection = self._projection_factory()
        projection.require_context(ctx)
        return projection

    @staticmethod
    def _tool(
        function: Callable[..., Any],
        tool_id: str,
        effects: set[ToolEffect],
        idempotency: IdempotencySemantics,
        *,
        output_policy: ToolOutputPolicy = _STATUS_OUTPUT_POLICY,
    ) -> HarnessTool:
        return HarnessTool(
            function,
            harness_metadata=HarnessToolMetadata(
                tool_id=tool_id,
                effects=frozenset(effects),
                credential_audiences=(),
                idempotency=idempotency,
                output_policy=output_policy,
            ),
        )


def _sequence(subagent_id: str) -> int:
    match = _SUBAGENT_ID_PATTERN.fullmatch(subagent_id)
    if match is None:
        raise ValueError("invalid managed subagent reference")
    return int(match.group(1))


def _validate_snapshot_identity(snapshot: SubagentExecutionSnapshot, backend_id: str) -> None:
    from a13n_harness.capabilities.subagents import SubagentExecutionSnapshot

    if not isinstance(snapshot, SubagentExecutionSnapshot):
        raise TypeError("subagent backend returned an invalid snapshot")
    if snapshot.backend_id != backend_id:
        raise StateError(
            "Subagent backend retargeted a managed execution.",
            code="subagent_backend_identity_mismatch",
        )


async def _cancel_accepted_subagent(
    operator: SubagentOperator,
    context: AgentContext,
    backend_id: str,
) -> None:
    task = asyncio.create_task(operator.cancel(context, backend_id))
    cancelled = False
    while True:
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                break
            cancelled = True
            continue
        except Exception:
            break
        break
    if cancelled:
        raise asyncio.CancelledError


def _detach_json(value: JsonValue | None) -> JsonValue | None:
    if value is None:
        return None
    return _JSON_ADAPTER.validate_python(deepcopy(value), strict=True)


def _intersect_usage_limits(*limits: UsageLimits | None) -> UsageLimits | None:
    selected = tuple(item for item in limits if item is not None)
    if not selected:
        return None
    numeric_fields = (
        "cost_limit",
        "request_limit",
        "tool_calls_limit",
        "input_tokens_limit",
        "output_tokens_limit",
        "total_tokens_limit",
        "per_request_input_tokens_limit",
    )
    values: dict[str, Any] = {}
    for field_name in numeric_fields:
        candidates = [getattr(item, field_name) for item in selected if getattr(item, field_name) is not None]
        values[field_name] = min(candidates) if candidates else None
    values["count_tokens_before_request"] = any(item.count_tokens_before_request for item in selected)
    return UsageLimits(**values)


__all__ = [
    "ManagedSubagentState",
    "SubagentManagerState",
    "SubagentManagerToolset",
]
