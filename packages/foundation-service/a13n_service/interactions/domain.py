"""Durable interaction identities and lifecycle values."""

from __future__ import annotations

import secrets
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from a13n_harness import SafeFailure
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from a13n_service.iam.domain import PrincipalRef
from a13n_service.ids import new_object_id
from a13n_service.models.domain import ModelExecutionObservation
from a13n_service.temporal import require_aware_utc

ObjectId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]{1,7}_[a-z0-9]{16,64}$")]
ThreadId = Annotated[
    str,
    StringConstraints(
        pattern=r"^(?:thread-[a-f0-9]{32}|[a-z][a-z0-9]{1,7}_[a-z0-9]{16,64})$",
        max_length=72,
    ),
]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
SchemaVersion = Annotated[str, StringConstraints(min_length=1, max_length=32)]
BoundedKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")]
BoundedName = Annotated[str, StringConstraints(min_length=1, max_length=256)]
JsonObject = dict[str, JsonValue]


def _utc(value: datetime) -> datetime:
    try:
        return require_aware_utc(value)
    except ValueError as error:
        raise ValueError("timestamp must include a UTC offset") from error


UtcDateTime = Annotated[datetime, AfterValidator(_utc)]


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
    runner_rotation = "runner_rotation"


class RunPayloadObjectRef(StrictModel):
    object_key: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    digest_sha256: Sha256Digest
    size_bytes: int = Field(ge=1)
    content_type: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    schema_version: SchemaVersion


class EncryptedRunConfigPayloadRef(StrictModel):
    object_key: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    ciphertext_digest_sha256: Sha256Digest
    protected_value_digest_sha256: Sha256Digest
    size_bytes: int = Field(ge=1)
    encryption_key_id: BoundedName
    schema_version: SchemaVersion


class PendingCallSummary(StrictModel):
    call_id: BoundedName
    kind: PendingCallKind
    tool_name: BoundedName | None = None
    provider_type: BoundedName | None = None
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


class RecoveryUsage(StrictModel):
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

    def plus(self, other: RecoveryUsage) -> RecoveryUsage:
        units = dict(self.billable_units)
        for key, amount in other.billable_units.items():
            units[key] = units.get(key, 0) + amount
        return RecoveryUsage(
            model_requests=self.model_requests + other.model_requests,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            tool_invocations=self.tool_invocations + other.tool_invocations,
            billable_units=units,
        )


class RecoveryUsageLimit(StrictModel):
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

    def permits(self, usage: RecoveryUsage) -> bool:
        scalar_limits = (
            (self.model_requests, usage.model_requests),
            (self.input_tokens, usage.input_tokens),
            (self.output_tokens, usage.output_tokens),
            (self.tool_invocations, usage.tool_invocations),
        )
        return all(limit is None or consumed <= limit for limit, consumed in scalar_limits) and all(
            usage.billable_units.get(key, 0) <= limit for key, limit in self.billable_units.items()
        )


class RecoveryBudget(StrictModel):
    policy_version: SchemaVersion
    max_recovery_attempts: int = Field(ge=0)
    max_handoffs: int = Field(ge=0)
    recovery_deadline_at: UtcDateTime | None = None
    max_usage: RecoveryUsageLimit | None = None


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
    tenant_id: ObjectId
    workspace_id: ObjectId
    created_at: UtcDateTime
    updated_at: UtcDateTime


class Thread(StrictModel):
    id: ThreadId
    version: int = Field(ge=1)
    queue_version: int = Field(ge=0)
    tenant_id: ObjectId
    session_id: ObjectId
    role: ThreadRole
    origin_kind: ThreadOriginKind
    origin_thread_id: ThreadId | None = None
    origin_run_id: ObjectId | None = None
    head_run_id: ObjectId | None = None
    current_run_id: ObjectId | None = None
    default_environment_id: ObjectId | None = None
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
    id: ObjectId
    version: int = Field(ge=1)
    tenant_id: ObjectId
    authority_principal: PrincipalRef
    session_id: ObjectId
    thread_id: ThreadId
    parent_run_id: ObjectId | None = None
    retry_of_run_id: ObjectId | None = None
    lineage_kind: RunLineageKind
    trigger_type: BoundedName
    trigger_entity_type: BoundedName | None = None
    trigger_entity_id: BoundedName | None = None
    parent_agent_instance_id: BoundedName | None = None
    delegation_id: BoundedName | None = None
    parent_tool_call_id: BoundedName | None = None
    agent_id: ObjectId
    agent_revision_id: ObjectId
    environment_id: ObjectId | None = None
    environment_access: Literal["read_only", "read_write", "full"] | None = None
    environment_use_started_at: UtcDateTime | None = None
    effective_agent_config_digest: Sha256Digest
    encrypted_config_payload: EncryptedRunConfigPayloadRef | None = None
    runtime_lock_digest: Sha256Digest
    model_execution_observation: ModelExecutionObservation
    connector_connection_selections: tuple[JsonObject, ...] = Field(default=(), max_length=512)
    mcp_connection_selections: tuple[JsonObject, ...] = Field(default=(), max_length=512)
    native_tool_contexts: tuple[JsonObject, ...] = Field(default=(), max_length=128, repr=False)
    priority: int
    queue_name: BoundedName
    available_at: UtcDateTime
    current_run_attempt_id: ObjectId | None = None
    next_attempt_fence: int = Field(ge=1)
    recovery_budget: RecoveryBudget
    attempts_started: int = Field(ge=0)
    recovery_attempts_started: int = Field(ge=0)
    handoffs_completed: int = Field(ge=0)
    usage_charged: RecoveryUsage
    idempotency_key: BoundedName | None = None
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
        input_inline = "input" in self.model_fields_set
        if input_inline == (self.input_object is not None):
            raise ValueError("Run input requires exactly one inline or object representation")
        if self.lineage_kind is RunLineageKind.root:
            if self.parent_run_id is not None:
                raise ValueError("root Run lineage cannot name a parent")
        elif self.parent_run_id is None:
            raise ValueError("continue and fork Run lineage require a parent")
        if self.recovery_attempts_started > self.recovery_budget.max_recovery_attempts:
            raise ValueError("recovery attempt count exceeds the accepted budget")
        if self.handoffs_completed > self.recovery_budget.max_handoffs:
            raise ValueError("handoff count exceeds the accepted budget")
        if not self.recovery_attempts_started <= self.attempts_started:
            raise ValueError("total Attempt count cannot be smaller than recovery generations")
        if self.attempts_started > self.recovery_attempts_started + self.handoffs_completed:
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
    tenant_id: ObjectId
    run_id: ObjectId
    attempt_number: int = Field(ge=1)
    fence: int = Field(ge=1)
    status: RunAttemptStatus
    replaces_run_attempt_id: ObjectId | None = None
    recovery_reason: BoundedName | None = None
    worker_id: BoundedName
    worker_generation: BoundedName
    worker_build_id: BoundedName
    runtime_lock_digest: Sha256Digest
    harness_run_id: BoundedName | None = None
    model_execution_observation: ModelExecutionObservation
    lease_token_digest: Sha256Digest
    lease_expires_at: UtcDateTime
    heartbeat_at: UtcDateTime
    usage: RecoveryUsage
    yield_reason: RunAttemptYieldReason | None = None
    failure: SafeFailure | None = None
    created_at: UtcDateTime
    claimed_at: UtcDateTime
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
        if self.status is RunAttemptStatus.succeeded and self.harness_run_id is None:
            raise ValueError("succeeded Attempt must have entered Harness")
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
    return f"thread-{secrets.token_hex(16)}"


def new_run_id() -> str:
    return new_object_id("run")


def new_run_attempt_id() -> str:
    return new_object_id("rat")


__all__ = [
    "BoundedKey",
    "EncryptedRunConfigPayloadRef",
    "JsonObject",
    "ObjectId",
    "PendingCallKind",
    "PendingCallSummary",
    "RecoveryBudget",
    "RecoveryUsage",
    "RecoveryUsageLimit",
    "Run",
    "RunAttempt",
    "RunAttemptStatus",
    "RunAttemptYieldReason",
    "RunInputKind",
    "RunLineageKind",
    "RunPayloadObjectRef",
    "RunPendingSummary",
    "RunStatus",
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
