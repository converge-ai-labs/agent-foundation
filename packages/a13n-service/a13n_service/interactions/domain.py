"""Durable interaction identities and lifecycle values."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from a13n_harness import SafeFailure
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from a13n_service.agent_configuration.context import ConfigurationRunContext
from a13n_service.digests import Sha256Digest
from a13n_service.iam.domain import PrincipalRef
from a13n_service.ids import ObjectId, new_object_id
from a13n_service.labels import Labels
from a13n_service.memory.bots.binding import BotMemoryBinding
from a13n_service.models.domain import ModelExecutionObservation
from a13n_service.temporal import UtcDateTime

ThreadId = Annotated[
    str,
    StringConstraints(
        pattern=r"^(?:thread-[a-f0-9]{32}|[a-z][a-z0-9]{1,7}_[a-z0-9]{16,64})$",
        max_length=72,
    ),
]
SchemaVersion = Annotated[str, StringConstraints(min_length=1, max_length=32)]
BoundedKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")]
BoundedText = Annotated[str, StringConstraints(min_length=1, max_length=256)]
JsonObject = dict[str, JsonValue]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ThreadRole(StrEnum):
    root = "root"
    child = "child"


class ThreadOriginKind(StrEnum):
    new = "new"
    fork = "fork"
    child = "child"


class RunLineageKind(StrEnum):
    root = "root"
    continue_ = "continue"
    fork = "fork"


class RunInputKind(StrEnum):
    agent_input = "agent_input"
    waiting_feedback = "waiting_feedback"
    waiting_continue = "waiting_continue"
    async_subagent_result = "async_subagent_result"


class RunStatus(StrEnum):
    accepted = "accepted"
    running = "running"
    waiting = "waiting"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class RunWaitReason(StrEnum):
    approval = "approval"
    client_tool = "client_tool"
    user_input = "user_input"
    multiple = "multiple"


class PendingCallKind(StrEnum):
    approval = "approval"
    client_tool = "client_tool"
    user_input = "user_input"


class RunAttemptStatus(StrEnum):
    leased = "leased"
    running = "running"
    succeeded = "succeeded"
    yielded = "yielded"
    failed = "failed"
    cancelled = "cancelled"


class RunAttemptYieldReason(StrEnum):
    service_drain = "service_drain"


class RunPayloadObjectRef(StrictModel):
    object_key: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    digest_sha256: Sha256Digest
    size_bytes: int = Field(ge=1)
    content_type: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    schema_version: SchemaVersion


class PendingCallSummary(StrictModel):
    call_id: BoundedText
    kind: PendingCallKind
    tool_name: BoundedText | None = None
    provider_type: BoundedText | None = None
    arguments_digest_sha256: Sha256Digest | None = None
    presentation: JsonObject | None = None


class RunPendingSummary(StrictModel):
    schema_version: Literal["1"] = "1"
    calls: tuple[PendingCallSummary, ...] = Field(min_length=1, max_length=256)
    resolution_policy: Literal["all"] = "all"

    @field_validator("calls")
    @classmethod
    def call_ids_are_unique(cls, value: tuple[PendingCallSummary, ...]) -> tuple[PendingCallSummary, ...]:
        call_ids = tuple(call.call_id for call in value)
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("pending call IDs must be unique")
        return value


class RunUsage(StrictModel):
    schema_version: Literal["1"] = "1"
    model_requests: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    tool_invocations: int = Field(default=0, ge=0)
    billable_units: dict[BoundedKey, int] = Field(default_factory=dict, max_length=128)

    @field_validator("billable_units")
    @classmethod
    def billable_units_are_non_negative(cls, value: dict[str, int]) -> dict[str, int]:
        if any(amount < 0 for amount in value.values()):
            raise ValueError("billable units must be non-negative")
        return value

    def plus(self, other: RunUsage) -> RunUsage:
        units = dict(self.billable_units)
        for key, amount in other.billable_units.items():
            units[key] = units.get(key, 0) + amount
        return RunUsage(
            model_requests=self.model_requests + other.model_requests,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            tool_invocations=self.tool_invocations + other.tool_invocations,
            billable_units=units,
        )


class RunUsageLimit(StrictModel):
    schema_version: Literal["1"] = "1"
    model_requests: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    tool_invocations: int | None = Field(default=None, ge=0)
    billable_units: dict[BoundedKey, int] = Field(default_factory=dict, max_length=128)

    @field_validator("billable_units")
    @classmethod
    def billable_unit_limits_are_non_negative(cls, value: dict[str, int]) -> dict[str, int]:
        if any(amount < 0 for amount in value.values()):
            raise ValueError("billable unit limits must be non-negative")
        return value

    def permits(self, usage: RunUsage) -> bool:
        scalar_limits = (
            (self.model_requests, usage.model_requests),
            (self.input_tokens, usage.input_tokens),
            (self.output_tokens, usage.output_tokens),
            (self.tool_invocations, usage.tool_invocations),
        )
        return all(limit is None or consumed <= limit for limit, consumed in scalar_limits) and all(
            usage.billable_units.get(key, 0) <= limit for key, limit in self.billable_units.items()
        )


class ExecutionBudget(StrictModel):
    policy_version: SchemaVersion
    max_attempts: int = Field(ge=0)
    max_handoffs: int = Field(ge=0)
    execution_deadline_at: UtcDateTime | None = None
    max_usage: RunUsageLimit | None = None


class SealedRunState(StrictModel):
    digest_sha256: Sha256Digest
    size_bytes: int = Field(ge=1)
    content_type: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    envelope_schema_version: SchemaVersion
    harness_schema_version: SchemaVersion
    checkpoint_seq: int = Field(ge=0)
    committed_by_run_attempt_id: ObjectId | None = None


class Session(StrictModel):
    id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId
    labels: Labels = Field(default_factory=dict)
    created_at: UtcDateTime
    updated_at: UtcDateTime


class Thread(StrictModel):
    id: ThreadId
    version: int = Field(ge=1)
    queue_version: int = Field(ge=0)
    organization_id: ObjectId
    session_id: ObjectId
    role: ThreadRole
    origin_kind: ThreadOriginKind
    origin_thread_id: ThreadId | None = None
    origin_run_id: ObjectId | None = None
    head_run_id: ObjectId | None = None
    current_run_id: ObjectId | None = None
    default_environment_id: ObjectId | None = None
    labels: Labels = Field(default_factory=dict)
    created_at: UtcDateTime
    updated_at: UtcDateTime

    @model_validator(mode="after")
    def origin_is_coherent(self) -> Thread:
        if self.origin_kind is ThreadOriginKind.new:
            if self.role is not ThreadRole.root or self.origin_thread_id is not None or self.origin_run_id is not None:
                raise ValueError("new Thread origin requires a root with no source references")
        elif self.origin_thread_id is None or self.origin_run_id is None:
            raise ValueError("fork and child Thread origins require source references")
        if self.origin_kind is ThreadOriginKind.child and self.role is not ThreadRole.child:
            raise ValueError("child Thread origin requires the child role")
        return self


class Run(StrictModel):
    configuration_context: ConfigurationRunContext | None = None
    id: ObjectId
    version: int = Field(ge=1)
    organization_id: ObjectId
    authority_principal: PrincipalRef
    session_id: ObjectId
    thread_id: ThreadId
    labels: Labels = Field(default_factory=dict)
    parent_run_id: ObjectId | None = None
    retry_of_run_id: ObjectId | None = None
    lineage_kind: RunLineageKind
    trigger_type: BoundedText
    trigger_entity_type: BoundedText | None = None
    trigger_entity_id: BoundedText | None = None
    parent_agent_instance_id: BoundedText | None = None
    delegation_id: BoundedText | None = None
    parent_tool_call_id: BoundedText | None = None
    agent_id: ObjectId
    agent_revision_id: ObjectId | None
    environment_id: ObjectId | None = None
    environment_access: Literal["read_only", "read_write", "full"] | None = None
    environment_use_started_at: UtcDateTime | None = None
    effective_agent_config_digest: Sha256Digest
    model_execution_observation: ModelExecutionObservation
    connection_selections: tuple[JsonObject, ...] = Field(default=(), max_length=512)
    native_tool_contexts: tuple[JsonObject, ...] = Field(default=(), max_length=128, repr=False)
    bot_memory: BotMemoryBinding | None = None
    priority: int
    queue_name: BoundedText
    available_at: UtcDateTime
    current_run_attempt_id: ObjectId | None = None
    execution_budget: ExecutionBudget
    attempts_started: int = Field(ge=0)
    attempts_charged: int = Field(ge=0)
    handoffs_completed: int = Field(ge=0)
    usage_charged: RunUsage
    idempotency_key: BoundedText | None = None
    request_fingerprint: Sha256Digest
    status: RunStatus
    wait_reason: RunWaitReason | None = None
    input_kind: RunInputKind
    input: JsonValue | None = None
    input_object: RunPayloadObjectRef | None = None
    input_text: Annotated[str, StringConstraints(max_length=65536)] | None = None
    output: JsonValue | None = None
    output_object: RunPayloadObjectRef | None = None
    output_text: Annotated[str, StringConstraints(max_length=65536)] | None = None
    failure: SafeFailure | None = None
    pending: RunPendingSummary | None = None
    sealed_state: SealedRunState | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime
    started_at: UtcDateTime | None = None
    waiting_at: UtcDateTime | None = None
    completed_at: UtcDateTime | None = None
    sealed_at: UtcDateTime | None = None

    @model_validator(mode="after")
    def lifecycle_is_coherent(self) -> Run:
        if (self.agent_revision_id is None) != (self.configuration_context is not None):
            raise ValueError("Only protected configuration Runs omit an AgentRevision")
        if self.configuration_context is not None and (
            self.configuration_context.session_id != self.session_id
            or self.configuration_context.thread_id != self.thread_id
        ):
            raise ValueError("Configuration context must match Run interaction identity")
        input_inline = "input" in self.model_fields_set
        if input_inline == (self.input_object is not None):
            raise ValueError("Run input requires exactly one inline or object representation")
        if self.lineage_kind is RunLineageKind.root:
            if self.parent_run_id is not None:
                raise ValueError("root Run lineage cannot name a parent")
        elif self.parent_run_id is None:
            raise ValueError("continue and fork Run lineage require a parent")
        if self.attempts_charged > self.execution_budget.max_attempts:
            raise ValueError("recovery attempt count exceeds the accepted budget")
        if self.handoffs_completed > self.execution_budget.max_handoffs:
            raise ValueError("handoff count exceeds the accepted budget")
        if not self.attempts_charged <= self.attempts_started:
            raise ValueError("total Attempt count cannot be smaller than recovery generations")
        if self.attempts_started > self.attempts_charged + self.handoffs_completed:
            raise ValueError("Attempt count exceeds recovery and planned-handoff authority")
        sealed = self.status in {RunStatus.waiting, RunStatus.completed, RunStatus.failed, RunStatus.cancelled}
        if sealed != (self.sealed_at is not None):
            raise ValueError("sealed_at must match terminal or waiting Run status")
        if self.status is RunStatus.accepted:
            if self.current_run_attempt_id is not None or self.attempts_started != 0:
                raise ValueError("accepted Run cannot have an Attempt")
        elif self.status is not RunStatus.running and self.current_run_attempt_id is not None:
            raise ValueError("only a running Run can select a current Attempt")
        if self.status is RunStatus.waiting:
            if self.wait_reason is None or self.pending is None or self.sealed_state is None or self.waiting_at is None:
                raise ValueError("waiting Run requires reason, pending summary, state, and timestamp")
        elif self.wait_reason is not None or self.pending is not None or self.waiting_at is not None:
            raise ValueError("waiting fields are valid only for waiting Runs")
        output_inline = "output" in self.model_fields_set
        if self.status is RunStatus.completed:
            if output_inline == (self.output_object is not None):
                raise ValueError("completed Run requires exactly one output representation")
            if self.sealed_state is None or self.completed_at is None:
                raise ValueError("completed Run requires sealed state and completion timestamp")
        elif (
            output_inline
            or self.output_object is not None
            or self.output_text is not None
            or self.completed_at is not None
        ):
            raise ValueError("output fields are valid only for completed Runs")
        if self.status in {RunStatus.waiting, RunStatus.completed} and self.failure is not None:
            raise ValueError("successful sealed Runs cannot contain failure")
        if self.status in {RunStatus.waiting, RunStatus.completed}:
            sealed_state = self.sealed_state
            if sealed_state is None:
                raise ValueError("successful sealed Run requires selected state")
            if sealed_state.checkpoint_seq == 0:
                raise ValueError("successful sealed Run cannot select initial state")
            if sealed_state.committed_by_run_attempt_id is None:
                raise ValueError("successful sealed Run state requires committing Attempt identity")
        if self.status in {RunStatus.failed, RunStatus.cancelled} and self.failure is None:
            raise ValueError("failed and cancelled Runs require bounded failure")
        if self.status is RunStatus.cancelled and self.sealed_state is not None:
            raise ValueError("interrupt-driven cancelled Run cannot select in-flight state")
        return self


class RunAttempt(StrictModel):
    id: ObjectId
    version: int = Field(ge=1)
    organization_id: ObjectId
    run_id: ObjectId
    attempt_number: int = Field(ge=1)
    status: RunAttemptStatus
    replaces_run_attempt_id: ObjectId | None = None
    start_reason: BoundedText | None = None
    worker_id: BoundedText
    worker_build_id: BoundedText
    harness_run_id: BoundedText | None = None
    model_execution_observation: ModelExecutionObservation
    lease_token_digest: Sha256Digest
    lease_expires_at: UtcDateTime
    heartbeat_at: UtcDateTime
    usage: RunUsage
    yield_reason: RunAttemptYieldReason | None = None
    failure: SafeFailure | None = None
    created_at: UtcDateTime
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None
    updated_at: UtcDateTime

    @model_validator(mode="after")
    def lifecycle_is_coherent(self) -> RunAttempt:
        terminal = self.status in {
            RunAttemptStatus.succeeded,
            RunAttemptStatus.yielded,
            RunAttemptStatus.failed,
            RunAttemptStatus.cancelled,
        }
        if terminal != (self.finished_at is not None):
            raise ValueError("finished_at must match terminal Attempt status")
        if self.status is RunAttemptStatus.leased:
            if self.harness_run_id is not None or self.started_at is not None:
                raise ValueError("leased Attempt has not entered Harness")
        elif self.status is RunAttemptStatus.running:
            if self.harness_run_id is None or self.started_at is None:
                raise ValueError("running Attempt requires Harness identity and timestamp")
        elif (self.harness_run_id is None) != (self.started_at is None):
            raise ValueError("terminal Attempt Harness identity and timestamp must be present together")
        if self.status is RunAttemptStatus.yielded:
            if self.yield_reason is None or self.failure is not None:
                raise ValueError("yielded Attempt requires only a yield reason")
        elif self.yield_reason is not None:
            raise ValueError("yield reason is valid only for yielded Attempts")
        if self.status is RunAttemptStatus.failed and self.failure is None:
            raise ValueError("failed Attempt requires bounded failure")
        if (
            self.status in {RunAttemptStatus.leased, RunAttemptStatus.running, RunAttemptStatus.succeeded}
            and self.failure
        ):
            raise ValueError("active or successful Attempt cannot contain failure")
        return self


def new_session_id() -> str:
    return new_object_id("sess")


def new_thread_id() -> str:
    return new_object_id("thread")


def new_run_id() -> str:
    return new_object_id("run")


def new_run_attempt_id() -> str:
    return new_object_id("rat")


__all__ = [
    "BoundedKey",
    "ExecutionBudget",
    "JsonObject",
    "ObjectId",
    "PendingCallKind",
    "PendingCallSummary",
    "Run",
    "RunAttempt",
    "RunAttemptStatus",
    "RunAttemptYieldReason",
    "RunInputKind",
    "RunLineageKind",
    "RunPayloadObjectRef",
    "RunPendingSummary",
    "RunStatus",
    "RunUsage",
    "RunUsageLimit",
    "RunWaitReason",
    "SealedRunState",
    "Session",
    "Sha256Digest",
    "StrictModel",
    "Thread",
    "ThreadId",
    "ThreadOriginKind",
    "ThreadRole",
    "UtcDateTime",
    "new_run_attempt_id",
    "new_run_id",
    "new_session_id",
    "new_thread_id",
]


def accepted_run(
    *,
    now: datetime,
    id: ObjectId,
    organization_id: ObjectId,
    authority_principal: PrincipalRef,
    session_id: ObjectId,
    thread_id: ThreadId,
    parent_run_id: ObjectId | None = None,
    retry_of_run_id: ObjectId | None = None,
    lineage_kind: RunLineageKind,
    trigger_type: BoundedText,
    trigger_entity_type: BoundedText | None = None,
    trigger_entity_id: BoundedText | None = None,
    parent_agent_instance_id: BoundedText | None = None,
    delegation_id: BoundedText | None = None,
    parent_tool_call_id: BoundedText | None = None,
    agent_id: ObjectId,
    agent_revision_id: ObjectId | None,
    environment_id: ObjectId | None = None,
    environment_access: Literal["read_only", "read_write", "full"] | None = None,
    effective_agent_config_digest: Sha256Digest,
    model_execution_observation: ModelExecutionObservation,
    connection_selections: tuple[JsonObject, ...] = (),
    native_tool_contexts: tuple[JsonObject, ...] = (),
    configuration_context: ConfigurationRunContext | None = None,
    bot_memory: BotMemoryBinding | None = None,
    priority: int,
    queue_name: BoundedText,
    execution_budget: ExecutionBudget,
    idempotency_key: BoundedText | None = None,
    request_fingerprint: Sha256Digest,
    input_kind: RunInputKind,
    input: JsonValue | None = None,
    input_object: RunPayloadObjectRef | None = None,
    input_text: Annotated[str, StringConstraints(max_length=65536)] | None = None,
    labels: Labels | None = None,
) -> Run:
    """Construct fresh execution state from accepted intent, never from prior execution."""
    values: dict[str, object] = dict(
        id=id,
        organization_id=organization_id,
        authority_principal=authority_principal,
        session_id=session_id,
        thread_id=thread_id,
        parent_run_id=parent_run_id,
        retry_of_run_id=retry_of_run_id,
        lineage_kind=lineage_kind,
        trigger_type=trigger_type,
        trigger_entity_type=trigger_entity_type,
        trigger_entity_id=trigger_entity_id,
        parent_agent_instance_id=parent_agent_instance_id,
        delegation_id=delegation_id,
        parent_tool_call_id=parent_tool_call_id,
        agent_id=agent_id,
        agent_revision_id=agent_revision_id,
        environment_id=environment_id,
        environment_access=environment_access,
        effective_agent_config_digest=effective_agent_config_digest,
        model_execution_observation=model_execution_observation,
        connection_selections=connection_selections,
        native_tool_contexts=native_tool_contexts,
        configuration_context=configuration_context,
        bot_memory=bot_memory,
        priority=priority,
        queue_name=queue_name,
        execution_budget=execution_budget,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        input_kind=input_kind,
        input_text=input_text,
        labels=labels or {},
        version=1,
        available_at=now,
        attempts_started=0,
        attempts_charged=0,
        handoffs_completed=0,
        usage_charged=RunUsage(),
        status=RunStatus.accepted,
        created_at=now,
        updated_at=now,
    )
    if input_object is None:
        values["input"] = input
    else:
        if input is not None:
            raise ValueError("object-backed input cannot also contain inline input")
        values["input_object"] = input_object
    return Run.model_validate(values)
