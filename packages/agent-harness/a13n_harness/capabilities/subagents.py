"""Definition-selected inline and asynchronous subagent execution."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import secrets
import weakref
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, field_validator, model_validator
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.usage import RunUsage, UsageLimits

from a13n_harness._json import redact_bearer, redact_json
from a13n_harness.context import AgentContext, BuiltSubagent, RunBindings
from a13n_harness.errors import DefinitionError, StateError
from a13n_harness.events import HarnessEvent, HarnessRunResultEvent
from a13n_harness.input import RunInputValue
from a13n_harness.state import HarnessState

SUBAGENT_CAPABILITY_ID = "a13n.subagents"
_INLINE_SUBAGENT_STATE_VERSION = "1"
_CHILD_ID_PATTERN = re.compile(r"^(?P<name>[a-z][a-z0-9_-]{0,62})-(?P<suffix>[0-9a-f]{4})$")
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
_MAX_FAILURE_BYTES = 1024 * 1024
_MAX_ACTIVITY_OUTPUT_BYTES = 32 * 1024
_MAX_ACTIVITY_VALUE_BYTES = 32 * 1024
_MAX_RECENT_TOOL_CALLS = 20
_JSON_ADAPTER = TypeAdapter(JsonValue)

type SubagentExecutionMode = Literal["inline", "async"]
type SubagentStatus = Literal["running", "succeeded", "failed", "cancelled"]
type SubagentToolCallStatus = Literal["running", "success", "failed", "denied", "interrupted"]
type SubagentEventKind = Literal["started", "completion", "gap"]
type _OpenChild = Callable[
    [AgentContext, BuiltSubagent, RunInputValue, str, bool, UsageLimits | None],
    AbstractAsyncContextManager[RunBindings],
]
type SubagentBackendEventHook = Callable[[SubagentExecutionSnapshot], Awaitable[None]]
type SubagentEventHook = Callable[[SubagentEvent], Awaitable[None]]


class InlineSubagentState(BaseModel):
    """Latest complete continuation for one compact inline child reference."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    child_instance_id: str = Field(min_length=7, max_length=68)
    subagent_name: str = Field(min_length=1, max_length=63)
    child_definition_id: str = Field(min_length=1, max_length=256)
    state: HarnessState

    @model_validator(mode="after")
    def _validate_identity(self) -> InlineSubagentState:
        match = _CHILD_ID_PATTERN.fullmatch(self.child_instance_id)
        if match is None or match.group("name") != self.subagent_name:
            raise ValueError("inline child identity is not canonical")
        return self


class InlineSubagentManagerState(BaseModel):
    """Capability-owned map of stable inline child references to nested Harness State."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    children: dict[str, InlineSubagentState] = Field(default_factory=dict)

    @field_validator("children")
    @classmethod
    def _validate_keys(cls, value: dict[str, InlineSubagentState]) -> dict[str, InlineSubagentState]:
        if any(key != child.child_instance_id for key, child in value.items()):
            raise ValueError("inline subagent state keys must match child_instance_id")
        return value


class SubagentExecutionSnapshot(BaseModel):
    """Detached current projection returned by an asynchronous operator."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    backend_id: str = Field(min_length=1, max_length=512)
    status: SubagentStatus
    output: JsonValue | None = None
    failure: JsonValue | None = None
    resumable: bool = False
    thread_id: str | None = Field(default=None, min_length=1, max_length=256)
    usage: RunUsage = Field(default_factory=RunUsage)


class SubagentToolCallSnapshot(BaseModel):
    """Bounded current or recent Tool activity owned by one live backend execution."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    tool_call_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(min_length=1, max_length=256)
    status: SubagentToolCallStatus
    arguments: JsonValue | None = None
    result: JsonValue | None = None


class SubagentActivitySnapshot(BaseModel):
    """Bounded, process-local view of one execution's recent model and Tool activity."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    sequence: int = Field(ge=0)
    output_preview: str = ""
    output_truncated: bool = False
    active_tool_calls: tuple[SubagentToolCallSnapshot, ...] = Field(default=(), max_length=_MAX_RECENT_TOOL_CALLS)
    recent_tool_calls: tuple[SubagentToolCallSnapshot, ...] = Field(default=(), max_length=_MAX_RECENT_TOOL_CALLS)
    dropped_tool_calls: int = Field(default=0, ge=0)


@dataclass(frozen=True, slots=True)
class SubagentEvent:
    """Correlated non-authoritative observation delivered to stable Host hooks."""

    kind: SubagentEventKind
    thread_id: str
    run_id: str
    agent_instance_id: str
    host_refs: Mapping[str, str]
    subagent_id: str
    subagent_name: str
    child_thread_id: str | None
    backend_id: str
    status: SubagentStatus
    usage: RunUsage


class SubagentOperator:
    """Process-local execution boundary used by the standard subagent Toolsets."""

    @property
    def usage_limits(self) -> UsageLimits | None:
        """Return optional operator narrowing applied below authored limits."""
        return None

    def open_child(
        self,
        context: AgentContext,
        child: BuiltSubagent,
        input: RunInputValue,
        child_instance_id: str,
        continuation: bool,
        usage_limits: UsageLimits | None,
    ) -> AbstractAsyncContextManager[RunBindings]:
        """Open one fresh child authority scope for inline or asynchronous execution."""
        del context, child, input, child_instance_id, continuation, usage_limits
        raise DefinitionError(
            "The configured subagent operator cannot create child bindings.",
            code="subagent_operator_unavailable",
        )

    async def start(
        self,
        context: AgentContext,
        child: BuiltSubagent,
        input: RunInputValue,
        subagent_id: str,
        resume_from: str | None,
        usage_limits: UsageLimits | None,
        observer: SubagentBackendEventHook,
    ) -> SubagentExecutionSnapshot:
        del context, child, input, subagent_id, resume_from, usage_limits, observer
        raise ToolFailed("Asynchronous subagent execution is unavailable.")

    async def attach(
        self,
        context: AgentContext,
        backend_id: str,
        observer: SubagentBackendEventHook,
    ) -> SubagentExecutionSnapshot | None:
        """Attach the current parent Run observer and return the latest snapshot."""
        del context, backend_id, observer
        return None

    async def snapshot(self, context: AgentContext, backend_id: str) -> SubagentExecutionSnapshot | None:
        """Return one detached current snapshot without changing observation."""
        del context, backend_id
        return None

    async def activity(self, context: AgentContext, backend_id: str) -> SubagentActivitySnapshot | None:
        """Return bounded process-local activity when supported by the backend."""
        del context, backend_id
        return None

    async def wait(
        self,
        context: AgentContext,
        backend_id: str,
        timeout_seconds: float | None,
    ) -> SubagentExecutionSnapshot | None:
        del context, backend_id, timeout_seconds
        return None

    async def steer(self, context: AgentContext, backend_id: str, message: str) -> str | None:
        del context, backend_id, message
        return None

    async def cancel(self, context: AgentContext, backend_id: str) -> bool:
        del context, backend_id
        return False

    async def force_close(self) -> None:
        """Reject admission, force-cancel owned work, and finish only its cleanup."""


@dataclass(slots=True)
class _CanonicalSubagent:
    backend_id: str
    subagent_id: str
    subagent_name: str
    child_definition_id: str
    parent_thread_id: str
    parent_run_id: str
    parent_agent_instance_id: str
    parent_host_refs: Mapping[str, str]
    status: SubagentStatus = "running"
    output: JsonValue | None = None
    failure: JsonValue | None = None
    cleanup_failure: str | None = None
    state: HarnessState | None = None
    thread_id: str | None = None
    usage: RunUsage = field(default_factory=RunUsage)
    stream: Any | None = None
    task: asyncio.Task[None] | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    observer: _WeakObserver | None = None
    activity_sequence: int = 0
    output_preview: str = ""
    output_truncated: bool = False
    active_tool_calls: OrderedDict[str, SubagentToolCallSnapshot] = field(default_factory=OrderedDict)
    recent_tool_calls: deque[SubagentToolCallSnapshot] = field(default_factory=deque)
    dropped_tool_calls: int = 0

    def snapshot(self) -> SubagentExecutionSnapshot:
        return SubagentExecutionSnapshot(
            backend_id=self.backend_id,
            status=self.status,
            output=deepcopy(self.output),
            failure=deepcopy(self.failure),
            resumable=self.state is not None,
            thread_id=self.thread_id,
            usage=deepcopy(self.usage),
        )

    def activity_snapshot(self) -> SubagentActivitySnapshot:
        return SubagentActivitySnapshot(
            sequence=self.activity_sequence,
            output_preview=redact_bearer(self.output_preview),
            output_truncated=self.output_truncated,
            active_tool_calls=tuple(item.model_copy(deep=True) for item in self.active_tool_calls.values()),
            recent_tool_calls=tuple(item.model_copy(deep=True) for item in self.recent_tool_calls),
            dropped_tool_calls=self.dropped_tool_calls,
        )


class _WeakObserver:
    """Weak, replaceable reference that cannot retain a completed parent Run."""

    def __init__(self, callback: SubagentBackendEventHook) -> None:
        if inspect.ismethod(callback):
            self._reference: weakref.ReferenceType[Any] = weakref.WeakMethod(callback)
        else:
            try:
                self._reference = weakref.ref(callback)
            except TypeError as exc:
                raise TypeError("subagent observer must support weak references") from exc

    def get(self) -> SubagentBackendEventHook | None:
        return cast(SubagentBackendEventHook | None, self._reference())


class SubagentBindingOperator(SubagentOperator):
    """Bind inline child Runs without providing asynchronous execution ownership."""

    def __init__(self, open_child: _OpenChild) -> None:
        if not callable(open_child):
            raise TypeError("open_child must be callable")
        self._open_child = open_child

    def open_child(
        self,
        context: AgentContext,
        child: BuiltSubagent,
        input: RunInputValue,
        child_instance_id: str,
        continuation: bool,
        usage_limits: UsageLimits | None,
    ) -> AbstractAsyncContextManager[RunBindings]:
        manager = self._open_child(
            context,
            child,
            input,
            child_instance_id,
            continuation,
            deepcopy(usage_limits),
        )
        if not isinstance(manager, AbstractAsyncContextManager):
            raise TypeError("open_child must return an async context manager")
        return manager


class SubagentManager(SubagentOperator):
    """Default in-memory operator owning child tasks until generation shutdown."""

    def __init__(
        self,
        open_child: _OpenChild,
        *,
        usage_limits: UsageLimits | None = None,
        event_hooks: Sequence[SubagentEventHook] = (),
    ) -> None:
        if not callable(open_child):
            raise TypeError("open_child must be callable")
        hooks = tuple(event_hooks)
        if not all(callable(hook) for hook in hooks):
            raise TypeError("event_hooks must contain callables")
        self._open_child = open_child
        self._usage_limits = deepcopy(usage_limits)
        self._event_hooks = hooks
        self._records: dict[str, _CanonicalSubagent] = {}
        self._child_authority_ids: set[str] = set()
        self._delivery_tasks: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()
        self._force_close_task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def usage_limits(self) -> UsageLimits | None:
        return deepcopy(self._usage_limits)

    def open_child(
        self,
        context: AgentContext,
        child: BuiltSubagent,
        input: RunInputValue,
        child_instance_id: str,
        continuation: bool,
        usage_limits: UsageLimits | None,
    ) -> AbstractAsyncContextManager[RunBindings]:
        if self._closed:
            raise DefinitionError("The subagent manager is closed.", code="subagent_operator_unavailable")
        manager = self._open_child(
            context,
            child,
            input,
            child_instance_id,
            continuation,
            deepcopy(usage_limits),
        )
        if not isinstance(manager, AbstractAsyncContextManager):
            raise TypeError("open_child must return an async context manager")
        return manager

    async def start(
        self,
        context: AgentContext,
        child: BuiltSubagent,
        input: RunInputValue,
        subagent_id: str,
        resume_from: str | None,
        usage_limits: UsageLimits | None,
        observer: SubagentBackendEventHook,
    ) -> SubagentExecutionSnapshot:
        previous_state: HarnessState | None = None
        if resume_from is not None:
            async with self._lock:
                previous = self._records.get(resume_from)
                if previous is None:
                    raise ToolFailed("The previous subagent execution is no longer available.")
                if previous.status not in _TERMINAL_STATUSES or previous.state is None:
                    raise ToolFailed("The previous subagent execution is not resumable.")
                if previous.child_definition_id != child.definition.definition_id:
                    raise ToolFailed("The previous subagent definition is incompatible.")
                previous_state = previous.state.model_copy(deep=True)

        backend_id = f"subagent-backend-{secrets.token_hex(12)}"
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            bindings = await stack.enter_async_context(
                self.open_child(
                    context,
                    child,
                    input,
                    backend_id,
                    resume_from is not None,
                    usage_limits,
                )
            )
            if not isinstance(bindings, RunBindings):
                raise DefinitionError(
                    "Subagent binding factory returned an invalid value.",
                    code="subagent_binding_invalid",
                )
            _validate_child_lineage(context, bindings, backend_id)
            child_authority_id = bindings.instance.agent_instance_id
            record = _CanonicalSubagent(
                backend_id=backend_id,
                subagent_id=subagent_id,
                subagent_name=child.declaration.name,
                child_definition_id=child.definition.definition_id,
                parent_thread_id=context.thread_id,
                parent_run_id=context.run_id,
                parent_agent_instance_id=context.instance.agent_instance_id,
                parent_host_refs=MappingProxyType(dict(context.instance.host_refs)),
                observer=_WeakObserver(observer),
            )
            start_gate = asyncio.Event()
            async with self._lock:
                if self._closed:
                    raise ToolFailed("The subagent manager is closing.")
                if child_authority_id in self._child_authority_ids:
                    raise DefinitionError(
                        "Child bindings reused an existing authority identity.",
                        code="delegation_lineage_invalid",
                    )
                self._child_authority_ids.add(child_authority_id)
                self._records[backend_id] = record
                record.task = asyncio.create_task(
                    self._run(
                        record,
                        child,
                        input,
                        bindings,
                        stack,
                        previous_state,
                        usage_limits,
                        start_gate,
                    ),
                    name=f"subagent-{backend_id}",
                )
                record.task.add_done_callback(lambda task: self._settle_task(record, task))
        except BaseException:
            await stack.aclose()
            raise

        self._dispatch_hooks(record, "started")
        start_gate.set()
        return record.snapshot()

    async def attach(
        self,
        context: AgentContext,
        backend_id: str,
        observer: SubagentBackendEventHook,
    ) -> SubagentExecutionSnapshot | None:
        del context
        async with self._lock:
            record = self._records.get(backend_id)
            if record is None:
                return None
            record.observer = _WeakObserver(observer)
            return record.snapshot()

    async def snapshot(self, context: AgentContext, backend_id: str) -> SubagentExecutionSnapshot | None:
        del context
        async with self._lock:
            record = self._records.get(backend_id)
            return None if record is None else record.snapshot()

    async def activity(self, context: AgentContext, backend_id: str) -> SubagentActivitySnapshot | None:
        del context
        async with self._lock:
            record = self._records.get(backend_id)
            return None if record is None else record.activity_snapshot()

    async def wait(
        self,
        context: AgentContext,
        backend_id: str,
        timeout_seconds: float | None,
    ) -> SubagentExecutionSnapshot | None:
        del context
        async with self._lock:
            record = self._records.get(backend_id)
        if record is None:
            return None
        try:
            if timeout_seconds is None:
                await record.done.wait()
            else:
                await asyncio.wait_for(record.done.wait(), timeout_seconds)
        except TimeoutError:
            pass
        return record.snapshot()

    async def steer(self, context: AgentContext, backend_id: str, message: str) -> str | None:
        del context
        async with self._lock:
            record = self._records.get(backend_id)
            stream = None if record is None else record.stream
            status = None if record is None else record.status
        if stream is None or status != "running":
            return None
        return await stream.steer(message)

    async def cancel(self, context: AgentContext, backend_id: str) -> bool:
        del context
        async with self._lock:
            record = self._records.get(backend_id)
            if record is None or record.status != "running":
                return False
            stream = record.stream
            task = record.task
            if stream is not None:
                stream.cancel()
            elif task is not None:
                task.cancel()
        return True

    async def wait_idle(self) -> None:
        """Wait until all currently admitted child executions are terminal."""

        while True:
            async with self._lock:
                tasks = tuple(
                    record.task
                    for record in self._records.values()
                    if not record.done.is_set() and record.task is not None
                )
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    async def force_close(self) -> None:
        async with self._lock:
            close_task = self._force_close_task
            if close_task is None:
                self._closed = True
                close_task = asyncio.create_task(self._force_close_owned(), name="subagent-manager-force-close")
                self._force_close_task = close_task
        cancelled, error = await _await_owned_task(close_task)
        if cancelled:
            raise asyncio.CancelledError
        if error is not None:
            raise error

    async def _force_close_owned(self) -> None:
        async with self._lock:
            records = tuple(self._records.values())
            closing_records = tuple(record for record in records if not record.done.is_set())
            for record in records:
                if not record.done.is_set():
                    if record.stream is not None:
                        record.stream.cancel()
                    elif record.task is not None:
                        record.task.cancel()
        tasks = tuple(record.task for record in records if record.task is not None)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        while self._delivery_tasks:
            deliveries = tuple(self._delivery_tasks)
            await asyncio.gather(*deliveries, return_exceptions=True)
            self._delivery_tasks.difference_update(deliveries)
        async with self._lock:
            self._records.clear()
        errors = [
            RuntimeError(record.cleanup_failure) for record in closing_records if record.cleanup_failure is not None
        ]
        if errors:
            raise ExceptionGroup("Subagent binding cleanup failed.", errors)

    async def _run(
        self,
        record: _CanonicalSubagent,
        child: BuiltSubagent,
        input: RunInputValue,
        bindings: RunBindings,
        stack: AsyncExitStack,
        previous_state: HarnessState | None,
        usage_limits: UsageLimits | None,
        start_gate: asyncio.Event,
    ) -> None:
        status: SubagentStatus = "failed"
        output: JsonValue | None = None
        failure: JsonValue | None = None
        state: HarnessState | None = None
        thread_id: str | None = None
        usage = RunUsage()
        cancelled = False
        try:
            await start_gate.wait()
            stream = child.executable.stream(
                input,
                bindings=bindings,
                previous_state=previous_state,
                usage=usage,
                usage_limits=deepcopy(usage_limits),
            )
            record.stream = stream
            terminal = None
            async with stream:
                async for item in stream:
                    if isinstance(item, HarnessRunResultEvent):
                        terminal = item.result
                    elif isinstance(item, HarnessEvent):
                        _observe_activity(record, item)
            if terminal is None:
                raise RuntimeError("The asynchronous child ended without a terminal result.")
            state = terminal.state.model_copy(deep=True) if terminal.state is not None else None
            thread_id = terminal.state.thread_id if terminal.state is not None else None
            if terminal.status == "completed":
                status = "succeeded"
                output = _detach_json(terminal.output)
            elif terminal.status == "cancelled":
                status = "cancelled"
            else:
                status = "failed"
                failure = _bounded_json(
                    terminal.failure.model_dump(mode="json")
                    if terminal.failure is not None
                    else {"code": "subagent_failed"}
                )
        except asyncio.CancelledError:
            status = "cancelled"
            cancelled = True
        except Exception as exc:
            status = "failed"
            failure = {
                "code": "subagent_execution_failed",
                "message": _bounded_text(str(exc) or exc.__class__.__name__),
            }
        finally:
            record.stream = None
            cleanup_error: BaseException | None = None
            cleanup_task = asyncio.create_task(stack.aclose(), name=f"subagent-cleanup-{record.backend_id}")
            cleanup_cancelled, cleanup_error = await _await_owned_task(cleanup_task)
            cancelled = cancelled or cleanup_cancelled or isinstance(cleanup_error, asyncio.CancelledError)
            if cleanup_error is not None:
                status = "failed"
                output = None
                record.cleanup_failure = _bounded_text(str(cleanup_error) or cleanup_error.__class__.__name__)
                failure = {
                    "code": "subagent_binding_cleanup_failed",
                    "message": record.cleanup_failure,
                }
            record.status = status
            record.output = output
            record.failure = failure
            record.state = state
            record.thread_id = thread_id
            record.usage = deepcopy(usage)
            record.done.set()
            self._notify(record, "completion")
        if cancelled:
            raise asyncio.CancelledError

    def _notify(self, record: _CanonicalSubagent, kind: SubagentEventKind) -> None:
        observer = record.observer.get() if record.observer is not None else None
        if observer is not None:
            snapshot = record.snapshot()
            self._spawn_delivery(lambda: observer(snapshot))
        self._dispatch_hooks(record, kind)

    def _dispatch_hooks(self, record: _CanonicalSubagent, kind: SubagentEventKind) -> None:
        event = SubagentEvent(
            kind=kind,
            thread_id=record.parent_thread_id,
            run_id=record.parent_run_id,
            agent_instance_id=record.parent_agent_instance_id,
            host_refs=record.parent_host_refs,
            subagent_id=record.subagent_id,
            subagent_name=record.subagent_name,
            child_thread_id=record.thread_id,
            backend_id=record.backend_id,
            status=record.status,
            usage=deepcopy(record.usage),
        )
        for hook in self._event_hooks:
            self._spawn_delivery(lambda hook=hook: hook(event))

    def _spawn_delivery(self, delivery: Callable[[], Awaitable[None]]) -> None:
        async def deliver() -> None:
            try:
                await delivery()
            except Exception:
                pass

        task = asyncio.create_task(deliver(), name=f"subagent-delivery-{secrets.token_hex(6)}")
        self._delivery_tasks.add(task)
        task.add_done_callback(self._delivery_tasks.discard)

    @staticmethod
    def _settle_task(record: _CanonicalSubagent, task: asyncio.Task[None]) -> None:
        if record.done.is_set():
            return
        if task.cancelled():
            record.status = "cancelled"
        else:
            error = task.exception()
            if error is not None:
                record.status = "failed"
                record.failure = {
                    "code": "subagent_execution_failed",
                    "message": _bounded_text(str(error) or error.__class__.__name__),
                }
        record.stream = None
        record.done.set()


@dataclass(init=False)
class SubagentCapability(AbstractCapability[AgentContext]):
    """Fix one subagent execution mode, operator, and standard Toolset."""

    id = SUBAGENT_CAPABILITY_ID

    def __init__(self, *, execution: SubagentExecutionMode, operator: SubagentOperator) -> None:
        if execution not in {"inline", "async"}:
            raise ValueError("execution must be 'inline' or 'async'")
        if not isinstance(operator, SubagentOperator):
            raise TypeError("operator must be a SubagentOperator")
        self._execution: SubagentExecutionMode = execution
        self._operator: SubagentOperator = operator

    @property
    def execution(self) -> SubagentExecutionMode:
        return self._execution

    @property
    def operator(self) -> SubagentOperator:
        return self._operator

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(SUBAGENT_CAPABILITY_ID)
        if existing is not None:
            if not isinstance(existing, _SubagentActiveCapability):
                raise DefinitionError(
                    "Subagent Capability has an incompatible logical-run replacement.",
                    code="capability_type_mismatch",
                )
            return existing
        if SUBAGENT_CAPABILITY_ID not in ctx.deps._capability_provenance.definition_ids:
            raise DefinitionError(
                "SubagentCapability must originate from the Agent definition.",
                code="capability_scope_invalid",
            )

        if self.execution == "inline":
            state = (
                await ctx.deps.state.read(
                    SUBAGENT_CAPABILITY_ID,
                    InlineSubagentManagerState,
                    version=_INLINE_SUBAGENT_STATE_VERSION,
                )
                or InlineSubagentManagerState()
            )
            _validate_inline_subagent_state(state, ctx.deps)
            replacement: _SubagentActiveCapability = _InlineSubagentCapability(
                context=ctx.deps,
                operator=self.operator,
                state=state,
            )
        else:
            replacement = _AsyncSubagentCapability(
                context=ctx.deps,
                operator=self.operator,
            )
        ctx.deps._record_run_capability(SUBAGENT_CAPABILITY_ID, replacement)
        return replacement


@dataclass(init=False)
class _SubagentActiveCapability(SubagentCapability):
    def __init__(self, *, context: AgentContext, execution: SubagentExecutionMode, operator: SubagentOperator) -> None:
        super().__init__(execution=execution, operator=operator)
        self._context = context

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        if ctx.deps is not self._context:
            raise DefinitionError(
                "Subagent run replacement cannot cross logical runs.",
                code="capability_scope_invalid",
            )
        return self


@dataclass(init=False)
class _InlineSubagentCapability(_SubagentActiveCapability):
    def __init__(
        self,
        *,
        context: AgentContext,
        operator: SubagentOperator,
        state: InlineSubagentManagerState,
    ) -> None:
        from a13n_harness.toolsets.delegation import DelegationToolset

        super().__init__(context=context, execution="inline", operator=operator)
        self._toolset = DelegationToolset(owner=self, context=context, operator=operator, state=state)

    def get_toolset(self) -> AbstractToolset[AgentContext]:
        return self._toolset.get_toolset()


@dataclass(init=False)
class _AsyncSubagentCapability(_SubagentActiveCapability):
    def __init__(self, *, context: AgentContext, operator: SubagentOperator) -> None:
        from a13n_harness.toolsets.subagent_manager import SubagentManagerToolset, _AsyncSubagentProjection

        super().__init__(context=context, execution="async", operator=operator)
        self._projection = _AsyncSubagentProjection(operator=operator, children=context.subagents)
        self._toolset = SubagentManagerToolset(self._projection, children=context.subagents)

    def get_toolset(self) -> AbstractToolset[AgentContext]:
        return self._toolset.get_toolset()

    async def wrap_run(self, ctx: RunContext[AgentContext], *, handler: Callable[[], Awaitable[Any]]) -> Any:
        async with self._projection.active_run(ctx):
            return await handler()


async def _await_owned_task(task: asyncio.Task[Any]) -> tuple[bool, BaseException | None]:
    """Wait for owned work without letting repeated caller cancellation cancel it."""
    cancelled = False
    while True:
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if task.cancelled():
                return cancelled, exc
            cancelled = True
            continue
        except BaseException as exc:
            return cancelled, exc
        return cancelled, None


def _validate_child_lineage(parent: AgentContext, bindings: RunBindings, child_instance_id: str) -> None:
    instance = bindings.instance
    if (
        instance.agent_instance_id == parent.instance.agent_instance_id
        or instance.parent_agent_instance_id != parent.instance.agent_instance_id
        or instance.delegation_id != child_instance_id
    ):
        raise DefinitionError(
            "Child bindings do not reproduce the requested parent lineage.",
            code="delegation_lineage_invalid",
        )


def _validate_inline_subagent_state(state: InlineSubagentManagerState, context: AgentContext) -> None:
    _validate_inline_thread_identities(state, parent_thread_id=context.thread_id)
    for child_id, record in state.children.items():
        try:
            child = context.subagents.require(record.subagent_name)
        except KeyError as exc:
            raise StateError(
                "Inline subagent State references an unavailable child definition.",
                code="subagent_state_incompatible",
                details={"child_instance_id": child_id},
            ) from exc
        if child.definition.definition_id != record.child_definition_id:
            raise StateError(
                "Inline subagent State child definition is incompatible with the current Agent.",
                code="subagent_state_incompatible",
                details={"child_instance_id": child_id},
            )


def _validate_inline_thread_identities(state: InlineSubagentManagerState, *, parent_thread_id: str) -> None:
    seen = {parent_thread_id}
    pending = [state]
    while pending:
        current = pending.pop()
        for child_id, record in current.children.items():
            thread_id = record.state.thread_id
            if thread_id in seen:
                raise StateError(
                    "Inline subagent State reuses a Thread identity.",
                    code="subagent_state_incompatible",
                    details={"child_instance_id": child_id},
                )
            seen.add(thread_id)
            entry = record.state.agent_context_state.entries.get(SUBAGENT_CAPABILITY_ID)
            if entry is None:
                continue
            if entry.version != _INLINE_SUBAGENT_STATE_VERSION:
                raise StateError(
                    "Nested inline subagent State has an unsupported version.",
                    code="subagent_state_incompatible",
                    details={"child_instance_id": child_id},
                )
            try:
                pending.append(InlineSubagentManagerState.model_validate(entry.data))
            except ValueError as exc:
                raise StateError(
                    "Nested inline subagent State is invalid.",
                    code="subagent_state_incompatible",
                    details={"child_instance_id": child_id},
                ) from exc


def _observe_activity(record: _CanonicalSubagent, item: HarnessEvent) -> None:
    event = item.event
    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
        _append_output_preview(record, event.part.content)
        return
    if isinstance(event, PartStartEvent) and isinstance(event.part, ToolCallPart):
        _start_tool_activity(record, event.part)
        return
    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
        _append_output_preview(record, event.delta.content_delta)
        return
    if isinstance(event, FunctionToolCallEvent):
        _start_tool_activity(record, event.part)
        return
    if isinstance(event, FunctionToolResultEvent):
        part = event.part
        active = record.active_tool_calls.pop(part.tool_call_id, None)
        if isinstance(part, ToolReturnPart):
            tool_name = part.tool_name
            status: SubagentToolCallStatus = part.outcome
            result = event.content if event.content is not None else part.content
        elif isinstance(part, RetryPromptPart):
            tool_name = part.tool_name or (active.tool_name if active is not None else "unknown")
            status = "failed"
            result = part.content
        else:
            return
        completed = SubagentToolCallSnapshot(
            tool_call_id=part.tool_call_id,
            tool_name=tool_name,
            status=status,
            arguments=None if active is None else active.arguments,
            result=_bounded_activity_json(result),
        )
        if len(record.recent_tool_calls) >= _MAX_RECENT_TOOL_CALLS:
            record.recent_tool_calls.popleft()
            record.dropped_tool_calls += 1
        record.recent_tool_calls.append(completed)
        record.activity_sequence += 1


def _start_tool_activity(record: _CanonicalSubagent, part: ToolCallPart) -> None:
    try:
        arguments: Any = part.args_as_dict()
    except ValueError:
        arguments = part.args
    if part.tool_call_id not in record.active_tool_calls and len(record.active_tool_calls) >= _MAX_RECENT_TOOL_CALLS:
        record.active_tool_calls.popitem(last=False)
        record.dropped_tool_calls += 1
    record.active_tool_calls[part.tool_call_id] = SubagentToolCallSnapshot(
        tool_call_id=part.tool_call_id,
        tool_name=part.tool_name,
        status="running",
        arguments=_bounded_activity_json(arguments),
    )
    record.activity_sequence += 1


def _append_output_preview(record: _CanonicalSubagent, delta: str) -> None:
    if not delta:
        return
    combined = (record.output_preview + delta).encode()
    if len(combined) > _MAX_ACTIVITY_OUTPUT_BYTES:
        combined = combined[-_MAX_ACTIVITY_OUTPUT_BYTES:]
        record.output_truncated = True
    record.output_preview = combined.decode(errors="ignore")
    record.activity_sequence += 1


def _bounded_activity_json(value: Any) -> JsonValue | None:
    if value is None:
        return None
    detached = redact_json(_detach_json(value))
    try:
        encoded = json.dumps(detached, ensure_ascii=False, separators=(",", ":")).encode()
    except Exception:
        return _bounded_activity_text(str(value))
    if len(encoded) <= _MAX_ACTIVITY_VALUE_BYTES:
        return detached
    return {"truncated": True, "preview": _bounded_activity_text(str(detached))}


def _bounded_activity_text(value: str) -> str:
    encoded = value.encode()
    if len(encoded) <= _MAX_ACTIVITY_VALUE_BYTES:
        return value
    return encoded[:_MAX_ACTIVITY_VALUE_BYTES].decode(errors="ignore")


def _detach_json(value: Any) -> JsonValue:
    try:
        projected = _JSON_ADAPTER.dump_python(value, mode="json", warnings="error")
        return _JSON_ADAPTER.validate_python(deepcopy(projected), strict=True)
    except Exception:
        return str(value)


def _bounded_json(value: Any) -> JsonValue:
    detached = _detach_json(value)
    try:
        encoded = json.dumps(detached, ensure_ascii=False, separators=(",", ":")).encode()
    except Exception:
        return _bounded_text(str(value))
    if len(encoded) <= _MAX_FAILURE_BYTES:
        return detached
    return {"truncated": True, "preview": _bounded_text(str(value))}


def _bounded_text(value: str) -> str:
    encoded = value.encode()
    if len(encoded) <= _MAX_FAILURE_BYTES:
        return value
    return encoded[:_MAX_FAILURE_BYTES].decode(errors="ignore")


__all__ = [
    "SUBAGENT_CAPABILITY_ID",
    "InlineSubagentManagerState",
    "InlineSubagentState",
    "SubagentActivitySnapshot",
    "SubagentBackendEventHook",
    "SubagentCapability",
    "SubagentEvent",
    "SubagentEventHook",
    "SubagentEventKind",
    "SubagentExecutionMode",
    "SubagentExecutionSnapshot",
    "SubagentManager",
    "SubagentOperator",
    "SubagentStatus",
    "SubagentToolCallSnapshot",
    "SubagentToolCallStatus",
]
