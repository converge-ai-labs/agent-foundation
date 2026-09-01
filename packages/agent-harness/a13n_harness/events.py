"""Ordered process-local Harness event envelopes and run-local extensions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable
from weakref import WeakKeyDictionary

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, field_validator, model_validator
from pydantic_ai import RunContext
from pydantic_ai.messages import AgentStreamEvent

if TYPE_CHECKING:
    from a13n_harness.context import AgentContext

from a13n_harness._json import dump_json_bytes, redact_json
from a13n_harness.errors import RunError
from a13n_harness.result import HarnessRunResult

_EXTENSION_PAYLOAD_ADAPTER = TypeAdapter(JsonValue)
_MAX_EXTENSION_PAYLOAD_BYTES = 64 * 1024
type HarnessExtensionKind = Literal[
    "context",
    "state",
    "recovery",
    "invocation",
    "delegation",
    "usage",
    "lifecycle",
    "tool",
    "diagnostic",
]


class HarnessExtensionEvent(BaseModel):
    """One small Harness-owned observation absent from Pydantic AI's event vocabulary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(default="1", min_length=1, max_length=32)
    kind: HarnessExtensionKind
    payload: JsonValue

    @field_validator("payload", mode="before")
    @classmethod
    def _bound_payload(cls, value: Any) -> JsonValue:
        validated = _EXTENSION_PAYLOAD_ADAPTER.validate_python(value, strict=True)
        sanitized = redact_json(validated)
        if len(dump_json_bytes(sanitized)) > _MAX_EXTENSION_PAYLOAD_BYTES:
            raise ValueError("Harness extension payload is too large")
        return sanitized


class _FirstPartyPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ModelRequestStartedPayload(_FirstPartyPayload):
    type: Literal["model_request_started"] = "model_request_started"
    request_id: str = Field(pattern=r"^model-request-[1-9][0-9]*$", max_length=64)
    request_index: int = Field(ge=0)
    message_count: int = Field(ge=0)


class ModelRequestCompletedPayload(_FirstPartyPayload):
    type: Literal["model_request_completed"] = "model_request_completed"
    request_id: str = Field(pattern=r"^model-request-[1-9][0-9]*$", max_length=64)
    request_index: int = Field(ge=0)


class ModelRequestFailedPayload(_FirstPartyPayload):
    type: Literal["model_request_failed"] = "model_request_failed"
    request_id: str = Field(pattern=r"^model-request-[1-9][0-9]*$", max_length=64)
    request_index: int = Field(ge=0)
    error_code: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=128)


class ContextSnapshotPayload(_FirstPartyPayload):
    type: Literal["context_snapshot"] = "context_snapshot"
    request_index: int = Field(ge=0)
    request_tokens: int = Field(ge=0)
    trigger_tokens: int = Field(gt=0)


class ContextOperationStartedPayload(_FirstPartyPayload):
    type: Literal["handoff_started", "compaction_started"]
    operation_id: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _validate_operation_id(self) -> ContextOperationStartedPayload:
        _require_context_operation_prefix(self.type, self.operation_id)
        return self


class ContextOperationPreparedPayload(_FirstPartyPayload):
    type: Literal["handoff_prepared"]
    operation_id: str = Field(min_length=1, max_length=128)
    summary_size: int = Field(ge=0)
    files_count: int = Field(ge=0, le=64)

    @model_validator(mode="after")
    def _validate_operation_id(self) -> ContextOperationPreparedPayload:
        _require_context_operation_prefix(self.type, self.operation_id)
        return self


class ContextOperationCompletedPayload(_FirstPartyPayload):
    type: Literal["handoff_completed", "compaction_completed"]
    operation_id: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _validate_operation_id(self) -> ContextOperationCompletedPayload:
        _require_context_operation_prefix(self.type, self.operation_id)
        return self


class ContextOperationFailedPayload(_FirstPartyPayload):
    type: Literal["handoff_failed", "compaction_failed"]
    operation_id: str = Field(min_length=1, max_length=128)
    failed_phase: str = Field(min_length=1, max_length=128)
    error_code: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=128)
    retryable: bool

    @model_validator(mode="after")
    def _validate_operation_id(self) -> ContextOperationFailedPayload:
        _require_context_operation_prefix(self.type, self.operation_id)
        return self


def _require_context_operation_prefix(event_type: str, operation_id: str) -> None:
    expected_prefix = "compaction-" if event_type.startswith("compaction_") else "handoff-"
    if not operation_id.startswith(expected_prefix):
        raise ValueError("context operation identity does not match its event type")


class TaskEventProjection(_FirstPartyPayload):
    id: str = Field(pattern=r"^task-[1-9][0-9]*$", max_length=64)
    version: int = Field(ge=1)
    subject: str = Field(min_length=1, max_length=512)
    active_form: str | None = Field(default=None, min_length=1, max_length=512)
    status: Literal["pending", "in_progress", "completed"]
    owner: str | None = Field(default=None, min_length=1, max_length=256)
    blocks: tuple[str, ...] = Field(default=(), max_length=256)
    blocked_by: tuple[str, ...] = Field(default=(), max_length=256)


class TaskChangedPayload(_FirstPartyPayload):
    type: Literal["task_changed"] = "task_changed"
    operation_id: str = Field(pattern=r"^task-change-[A-Za-z0-9_-]+$", max_length=128)
    task_state_version: int = Field(ge=1)
    reason: Literal[
        "created",
        "updated",
        "claimed",
        "completed",
        "dependency_updated",
        "provider_observed",
    ]
    task: TaskEventProjection


class InlineDelegationPayload(_FirstPartyPayload):
    type: Literal["inline_delegation"] = "inline_delegation"
    invocation_id: str = Field(pattern=r"^delegation-[A-Za-z0-9_-]+$", max_length=128)
    action: Literal["started", "completed", "failed"]
    child_instance_id: str = Field(min_length=1, max_length=128)
    subagent: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=128)
    parent_run_id: str = Field(min_length=1, max_length=256)
    parent_agent_instance_id: str = Field(min_length=1, max_length=256)
    parent_tool_call_id: str | None = Field(default=None, min_length=1, max_length=256)
    child_run_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def _require_started_or_completed_child_run(self) -> InlineDelegationPayload:
        if self.action in {"started", "completed"} and self.child_run_id is None:
            raise ValueError("started and completed inline delegation events require child_run_id")
        return self


class SteeringInputEnqueuedPayload(_FirstPartyPayload):
    """One external steering input or Harness lifecycle notice accepted by the active Run."""

    type: Literal["steering_input_enqueued"] = "steering_input_enqueued"
    enqueue_id: str = Field(min_length=1, max_length=256)
    source: Literal["external", "async_subagent", "background_process"]
    references: tuple[str, ...] = Field(default=(), max_length=16)

    @field_validator("references")
    @classmethod
    def _validate_references(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not reference or len(reference) > 128 for reference in value):
            raise ValueError("steering references must be non-empty bounded strings")
        return value


class UsageReportPayload(_FirstPartyPayload):
    type: Literal["usage_report"] = "usage_report"
    report_id: str = Field(min_length=1, max_length=128)
    reason: Literal["model_request", "terminal"]
    trigger_record_id: str | None = Field(default=None, min_length=1, max_length=128)
    chunk_index: int = Field(ge=0)
    chunk_count: int = Field(gt=0)
    records: tuple[dict[str, JsonValue], ...] = Field(max_length=128)

    @model_validator(mode="after")
    def _validate_chunk_position(self) -> UsageReportPayload:
        if self.chunk_index >= self.chunk_count:
            raise ValueError("usage report chunk_index must be lower than chunk_count")
        if not self.records:
            raise ValueError("usage report must contain at least one record")
        return self


class CodeActExecutionStartedPayload(_FirstPartyPayload):
    type: Literal["codeact_execution_started"] = "codeact_execution_started"
    execution_id: str = Field(pattern=r"^codeact-[a-f0-9]{32}$", max_length=64)
    outer_tool_call_id: str = Field(min_length=1, max_length=256)
    kind: Literal["inline", "program"]
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$", max_length=64)
    source_path: str | None = Field(default=None, min_length=1, max_length=4096)
    catalog_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$", max_length=64)


class CodeActToolCallStartedPayload(_FirstPartyPayload):
    type: Literal["codeact_tool_call_started"] = "codeact_tool_call_started"
    execution_id: str = Field(pattern=r"^codeact-[a-f0-9]{32}$", max_length=64)
    nested_tool_call_id: str = Field(min_length=1, max_length=128)
    ordinal: int = Field(gt=0)
    canonical_tool_name: str = Field(min_length=1, max_length=256)
    sandbox_tool_name: str = Field(min_length=1, max_length=256)


class CodeActToolCallCompletedPayload(_FirstPartyPayload):
    type: Literal["codeact_tool_call_completed"] = "codeact_tool_call_completed"
    execution_id: str = Field(pattern=r"^codeact-[a-f0-9]{32}$", max_length=64)
    nested_tool_call_id: str = Field(min_length=1, max_length=128)
    outcome: Literal["completed", "failed", "cancelled", "deferred"]
    duration_ms: int = Field(ge=0)
    value_bytes: int | None = Field(default=None, ge=0)
    error_type: str | None = Field(default=None, min_length=1, max_length=128)
    side_effect_uncertain: bool


class CodeActExecutionCompletedPayload(_FirstPartyPayload):
    type: Literal["codeact_execution_completed"] = "codeact_execution_completed"
    execution_id: str = Field(pattern=r"^codeact-[a-f0-9]{32}$", max_length=64)
    status: Literal["completed", "failed", "timed_out", "cancelled"]
    duration_ms: int = Field(ge=0)
    call_count: int = Field(ge=0)
    cumulative_bytes: int = Field(ge=0)
    error_type: str | None = Field(default=None, min_length=1, max_length=128)
    side_effect_uncertain: bool


class FileChangeProjection(_FirstPartyPayload):
    """One confirmed logical file mutation without content disclosure."""

    path: str = Field(min_length=1, max_length=4096)
    action: Literal["created", "modified", "written", "deleted", "moved", "copied"]
    destination: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def _validate_destination(self) -> FileChangeProjection:
        if (self.action in {"moved", "copied"}) != (self.destination is not None):
            raise ValueError("file move and copy changes require exactly one destination")
        return self


class FilesystemChangedValue(_FirstPartyPayload):
    """Bounded confirmed file changes produced by one Tool call."""

    changes: tuple[FileChangeProjection, ...] = Field(min_length=1, max_length=256)


class ToolExtraEventPayload(_FirstPartyPayload):
    """Generic typed envelope for one Tool-owned semantic observation."""

    type: Literal["tool_extra"] = "tool_extra"
    tool_call_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(min_length=1, max_length=256)
    tool_id: str = Field(min_length=1, max_length=256)
    name: str = Field(pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$", max_length=256)
    value: JsonValue


type FirstPartyEventPayload = (
    ModelRequestStartedPayload
    | ModelRequestCompletedPayload
    | ModelRequestFailedPayload
    | ContextSnapshotPayload
    | ContextOperationStartedPayload
    | ContextOperationPreparedPayload
    | ContextOperationCompletedPayload
    | ContextOperationFailedPayload
    | TaskChangedPayload
    | InlineDelegationPayload
    | SteeringInputEnqueuedPayload
    | UsageReportPayload
    | CodeActExecutionStartedPayload
    | CodeActToolCallStartedPayload
    | CodeActToolCallCompletedPayload
    | CodeActExecutionCompletedPayload
    | ToolExtraEventPayload
)


@runtime_checkable
class AgentStreamEventProtocol(Protocol):
    """Minimal runtime surface shared by native and extended Pydantic AI stream events."""

    @property
    def event_kind(self) -> str: ...


type HarnessEventValue = AgentStreamEvent | AgentStreamEventProtocol | HarnessExtensionEvent


@dataclass(frozen=True, slots=True)
class HarnessEvent:
    """A public event correlated to one Thread and Harness Run."""

    thread_id: str
    run_id: str
    sequence: int
    occurred_at: datetime
    event: HarnessEventValue

    def __post_init__(self) -> None:
        if (
            not isinstance(self.thread_id, str)
            or not self.thread_id.strip()
            or not isinstance(self.run_id, str)
            or not self.run_id.strip()
            or not isinstance(self.sequence, int)
            or self.sequence < 0
        ):
            raise ValueError("Harness event correlation is invalid")


@dataclass(eq=False, slots=True, weakref_slot=True)
class _ChildEventProvenanceToken:
    pass


@dataclass(frozen=True, slots=True)
class _ChildEventProvenance:
    thread_id: str
    run_id: str
    sequence: int


@dataclass(frozen=True, slots=True)
class _ForwardedChildEvent(HarnessEvent):
    """Private child envelope carrying an opaque provenance lookup token."""

    _child_provenance_token: _ChildEventProvenanceToken = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class HarnessRunResultEvent[OutputT]:
    """The sole terminal stream event, emitted only after teardown succeeds."""

    thread_id: str
    run_id: str
    sequence: int
    occurred_at: datetime
    result: HarnessRunResult[OutputT]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.thread_id, str)
            or not self.thread_id.strip()
            or not isinstance(self.run_id, str)
            or not self.run_id.strip()
            or not isinstance(self.sequence, int)
            or self.sequence < 0
        ):
            raise ValueError("Harness result event correlation is invalid")
        if not isinstance(self.result, HarnessRunResult):
            raise TypeError("Harness result event requires HarnessRunResult")
        if self.result.thread_id != self.thread_id or self.result.run_id != self.run_id:
            raise ValueError("Harness result event correlation must match its result")


type HarnessStreamEvent[OutputT] = HarnessEvent | HarnessRunResultEvent[OutputT]


@runtime_checkable
class HarnessEventEmitter(Protocol):
    """Run-local path for Harness extensions into the canonical stream."""

    async def emit(self, event: HarnessExtensionEvent) -> None: ...


async def emit_harness_event(
    emitter: HarnessEventEmitter,
    *,
    kind: HarnessExtensionKind,
    payload: FirstPartyEventPayload,
) -> None:
    """Emit one first-party event from a validated typed payload."""
    await emitter.emit(
        HarnessExtensionEvent(
            kind=kind,
            payload=payload.model_dump(mode="json"),
        )
    )


async def emit_tool_event(
    ctx: RunContext[AgentContext],
    *,
    tool_id: str,
    name: str,
    value: BaseModel,
) -> None:
    """Emit one typed Tool-owned observation correlated to the active call."""
    if not ctx.tool_call_id or not ctx.tool_name:
        raise RunError("A Tool extra event requires active Tool correlation.", code="event_invalid")
    payload = ToolExtraEventPayload(
        tool_call_id=ctx.tool_call_id,
        tool_name=ctx.tool_name,
        tool_id=tool_id,
        name=name,
        value=value.model_dump(mode="json"),
    )
    await emit_harness_event(ctx.deps.events, kind="tool", payload=payload)


class _ChildEventForwarder:
    """Private proof that events were consumed from one exact child stream."""

    def __init__(self, parent: _RunEventEmitter, child: _RunEventEmitter) -> None:
        self._parent = parent
        self._child = child
        self._last_sequence_by_run: dict[str, int] = {}
        self._closed = False

    async def forward(self, event: HarnessEvent) -> None:
        if self._closed:
            raise RunError("The child event forwarder is closed.", code="child_event_invalid")
        if not isinstance(event, HarnessEvent) or event.sequence < 0:
            raise RunError("Forwarded child event is invalid.", code="child_event_invalid")
        if event.run_id == self._child.run_id:
            if event.thread_id != self._child.thread_id:
                raise RunError("Forwarded child event provenance is invalid.", code="child_event_invalid")
        elif not self._child.is_registered_child(event.run_id, event.thread_id):
            raise RunError("Forwarded child event provenance is invalid.", code="child_event_invalid")
        previous = self._last_sequence_by_run.get(event.run_id)
        if previous is not None and event.sequence <= previous:
            raise RunError("Forwarded child event sequence is invalid.", code="child_event_invalid")
        self._last_sequence_by_run[event.run_id] = event.sequence
        await self._parent._forward_from_child(event, source=self._child)

    def close(self) -> None:
        self._closed = True


class _RunEventEmitter:
    """Bounded queue consumed concurrently with the active Pydantic event iterator."""

    def __init__(self, thread_id: str, run_id: str, *, capacity: int = 64) -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self._queue: asyncio.Queue[HarnessExtensionEvent | HarnessEvent] = asyncio.Queue(maxsize=capacity)
        self._child_runs: dict[str, str] = {}
        self._child_provenance_by_token: WeakKeyDictionary[_ChildEventProvenanceToken, _ChildEventProvenance] = (
            WeakKeyDictionary()
        )
        self._consumer_started = False
        self._producer_stopped = asyncio.Event()
        self._producer_stopped.set()
        self._closed = False

    async def emit(self, event: HarnessExtensionEvent) -> None:
        if self._closed:
            raise RunError("The Harness event emitter is closed.", code="event_emitter_closed")
        if not isinstance(event, HarnessExtensionEvent):
            raise RunError("Harness extension event is invalid.", code="event_invalid")
        try:
            validated = HarnessExtensionEvent.model_validate(event.model_dump(), strict=True)
        except ValueError as exc:
            raise RunError("Harness extension event is invalid.", code="event_invalid") from exc
        await self._put(validated)

    def bind_child(self, child: _RunEventEmitter) -> _ChildEventForwarder:
        """Create a private forwarding proof for one exact child stream emitter."""
        if (
            self._closed
            or child._closed
            or child is self
            or not child.run_id.strip()
            or child.run_id == self.run_id
            or child.thread_id == self.thread_id
        ):
            raise RunError("Inline child correlation is invalid.", code="child_event_invalid")
        self._child_runs[child.run_id] = child.thread_id
        return _ChildEventForwarder(self, child)

    async def _forward_from_child(self, event: HarnessEvent, *, source: _RunEventEmitter) -> None:
        if self._closed:
            raise RunError("The Harness event emitter is closed.", code="event_emitter_closed")
        if event.run_id == source.run_id:
            if event.thread_id != source.thread_id:
                raise RunError("Forwarded child event is invalid.", code="child_event_invalid")
        elif not source.is_registered_child(event.run_id, event.thread_id):
            raise RunError("Forwarded child event is invalid.", code="child_event_invalid")
        self._child_runs[event.run_id] = event.thread_id
        token = _ChildEventProvenanceToken()
        self._child_provenance_by_token[token] = _ChildEventProvenance(
            thread_id=event.thread_id,
            run_id=event.run_id,
            sequence=event.sequence,
        )
        await self._put(
            _ForwardedChildEvent(
                thread_id=event.thread_id,
                run_id=event.run_id,
                sequence=event.sequence,
                occurred_at=event.occurred_at,
                event=event.event,
                _child_provenance_token=token,
            )
        )

    def take_child_provenance(self, event: HarnessEvent) -> _ChildEventProvenance | None:
        if not isinstance(event, _ForwardedChildEvent):
            return None
        return self._child_provenance_by_token.pop(event._child_provenance_token, None)

    def is_registered_child(self, run_id: str, thread_id: str) -> bool:
        return self._child_runs.get(run_id) == thread_id

    def start_consuming(self) -> None:
        if self._closed:
            raise RunError("The Harness event emitter is closed.", code="event_emitter_closed")
        self._consumer_started = True
        self._producer_stopped.clear()

    def stop_consuming(self) -> None:
        self._consumer_started = False
        self._producer_stopped.set()

    async def _put(self, event: HarnessExtensionEvent | HarnessEvent) -> None:
        if self._consumer_started:
            put = asyncio.create_task(self._queue.put(event))
            stopped = asyncio.create_task(self._producer_stopped.wait())
            try:
                done, _ = await asyncio.wait({put, stopped}, return_when=asyncio.FIRST_COMPLETED)
                if put in done:
                    put.result()
                    return
                raise RunError(
                    "The Harness event consumer stopped before accepting an event.",
                    code="event_emitter_closed" if self._closed else "event_consumer_stopped",
                )
            finally:
                for task in (put, stopped):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(put, stopped, return_exceptions=True)
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull as exc:
            raise RunError("The Harness event buffer is full.", code="event_buffer_full") from exc

    async def next(self) -> HarnessExtensionEvent | HarnessEvent:
        return await self._queue.get()

    def get_nowait(self) -> HarnessExtensionEvent | HarnessEvent:
        return self._queue.get_nowait()

    def empty(self) -> bool:
        return self._queue.empty()

    def close(self) -> None:
        self._closed = True
        self._consumer_started = False
        self._child_provenance_by_token.clear()
        self._producer_stopped.set()

    def envelope(self, event: HarnessExtensionEvent, *, sequence: int) -> HarnessEvent:
        return HarnessEvent(
            thread_id=self.thread_id,
            run_id=self.run_id,
            sequence=sequence,
            occurred_at=datetime.now(UTC),
            event=event,
        )
