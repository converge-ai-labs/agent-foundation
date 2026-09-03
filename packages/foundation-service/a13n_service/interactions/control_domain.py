"""Durable values for Agent continuation, active control, and queued intent."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Annotated, Literal

import rfc8785
from pydantic import Field, JsonValue, StringConstraints, field_validator, model_validator

from a13n_service.agents.domain import AgentRunOverride
from a13n_service.iam.domain import PrincipalRef
from a13n_service.ids import new_object_id

from .domain import (
    BoundedKey,
    BoundedName,
    ObjectId,
    PendingCallKind,
    PendingCallSummary,
    RunPendingSummary,
    Sha256Digest,
    StrictModel,
    ThreadId,
    UtcDateTime,
)
from .input import AcceptedAgentInput, AgentInput

_MAX_HOOK_NAMES = 128


class WebhookDestinationConfig(StrictModel):
    endpoint_url: Annotated[str, StringConstraints(min_length=1, max_length=8192)]
    signing_secret_id: ObjectId
    signature_profile: Literal["hmac_sha256_v1"] = "hmac_sha256_v1"


class InlineHookSubscriptionInput(StrictModel):
    hook_names: tuple[BoundedKey, ...] = Field(min_length=1, max_length=_MAX_HOOK_NAMES)
    webhook: WebhookDestinationConfig

    @field_validator("hook_names")
    @classmethod
    def names_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("hook names must be unique")
        return value


class ThreadRunSubmissionIntent(StrictModel):
    input: AgentInput
    agent_id: ObjectId | None = None
    agent_revision_id: ObjectId | None = None
    expected_current_revision_id: ObjectId | None = None
    config_override: AgentRunOverride | None = None
    hook_subscription: InlineHookSubscriptionInput | None = None

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self)

    def digest_sha256(self) -> str:
        return _sha256(self.canonical_bytes())


class WaitingResolutionDefaults(StrictModel):
    mode: Literal["defaults"] = "defaults"
    sealed_state_digest_sha256: Sha256Digest


class ThreadRunSubmissionRequest(StrictModel):
    expected_thread_version: int = Field(ge=1)
    input: AgentInput
    agent_id: ObjectId | None = None
    agent_revision_id: ObjectId | None = None
    expected_current_revision_id: ObjectId | None = None
    config_override: AgentRunOverride | None = None
    hook_subscription: InlineHookSubscriptionInput | None = None
    waiting_resolution: WaitingResolutionDefaults | None = None

    def intent(self) -> ThreadRunSubmissionIntent:
        return ThreadRunSubmissionIntent.model_validate(
            self.model_dump(mode="python", exclude={"expected_thread_version", "waiting_resolution"})
        )


class SubmittedPendingAction(StrEnum):
    approve = "approve"
    reject = "reject"
    complete = "complete"
    respond = "respond"


class ApprovePendingResolution(StrictModel):
    call_id: BoundedName
    action: Literal[SubmittedPendingAction.approve] = SubmittedPendingAction.approve


class RejectPendingResolution(StrictModel):
    call_id: BoundedName
    action: Literal[SubmittedPendingAction.reject] = SubmittedPendingAction.reject


class CompletePendingResolution(StrictModel):
    call_id: BoundedName
    action: Literal[SubmittedPendingAction.complete] = SubmittedPendingAction.complete
    result: JsonValue


class RespondPendingResolution(StrictModel):
    call_id: BoundedName
    action: Literal[SubmittedPendingAction.respond] = SubmittedPendingAction.respond
    response: JsonValue


SubmittedPendingResolution = Annotated[
    ApprovePendingResolution | RejectPendingResolution | CompletePendingResolution | RespondPendingResolution,
    Field(discriminator="action"),
]


class WaitingRunFeedbackRequest(StrictModel):
    expected_thread_version: int = Field(ge=1)
    sealed_state_digest_sha256: Sha256Digest
    resolutions: tuple[SubmittedPendingResolution, ...] = Field(default=(), max_length=256)
    hook_subscription: InlineHookSubscriptionInput | None = None

    @field_validator("resolutions")
    @classmethod
    def call_ids_are_unique(
        cls,
        value: tuple[SubmittedPendingResolution, ...],
    ) -> tuple[SubmittedPendingResolution, ...]:
        call_ids = tuple(item.call_id for item in value)
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("submitted feedback call IDs must be unique")
        return value


class PendingResolutionOutcome(StrEnum):
    approve = "approve"
    reject = "reject"
    complete = "complete"
    respond = "respond"
    no_response = "no_response"


class AcceptedPendingResolution(StrictModel):
    call_id: BoundedName
    kind: PendingCallKind
    outcome: PendingResolutionOutcome
    result: JsonValue | None = None

    @model_validator(mode="after")
    def outcome_matches_kind(self) -> AcceptedPendingResolution:
        allowed = {
            PendingCallKind.approval: {
                PendingResolutionOutcome.approve,
                PendingResolutionOutcome.reject,
            },
            PendingCallKind.client_tool: {
                PendingResolutionOutcome.complete,
                PendingResolutionOutcome.no_response,
            },
            PendingCallKind.user_input: {
                PendingResolutionOutcome.respond,
                PendingResolutionOutcome.no_response,
            },
        }
        if self.outcome not in allowed[self.kind]:
            raise ValueError("accepted feedback outcome does not match pending kind")
        if (
            self.outcome
            in {
                PendingResolutionOutcome.approve,
                PendingResolutionOutcome.reject,
                PendingResolutionOutcome.no_response,
            }
            and self.result is not None
        ):
            raise ValueError("accepted feedback outcome cannot carry a result")
        return self


class WaitingRunFeedback(StrictModel):
    schema_version: Literal["1"] = "1"
    waiting_run_id: ObjectId
    sealed_state_digest_sha256: Sha256Digest
    resolutions: tuple[AcceptedPendingResolution, ...] = Field(max_length=256)

    @field_validator("resolutions")
    @classmethod
    def accepted_call_ids_are_unique(
        cls,
        value: tuple[AcceptedPendingResolution, ...],
    ) -> tuple[AcceptedPendingResolution, ...]:
        if len(value) != len({item.call_id for item in value}):
            raise ValueError("accepted feedback call IDs must be unique")
        return value


class WaitingRunContinueInput(WaitingRunFeedback):
    input: AcceptedAgentInput


def normalize_feedback(
    *,
    waiting_run_id: str,
    sealed_state_digest_sha256: str,
    pending: RunPendingSummary,
    submitted: tuple[SubmittedPendingResolution, ...],
) -> WaitingRunFeedback:
    """Expand one submitted subset into the complete frozen pending order."""

    by_id = {item.call_id: item for item in submitted}
    pending_by_id = {item.call_id: item for item in pending.calls}
    unknown = by_id.keys() - pending_by_id.keys()
    if unknown:
        raise ControlValidationError("feedback_call_unknown", "Feedback names an unknown pending call")
    normalized = tuple(_normalize_resolution(call, by_id.get(call.call_id)) for call in pending.calls)
    return WaitingRunFeedback(
        waiting_run_id=waiting_run_id,
        sealed_state_digest_sha256=sealed_state_digest_sha256,
        resolutions=normalized,
    )


def normalize_waiting_continue(
    *,
    waiting_run_id: str,
    sealed_state_digest_sha256: str,
    pending: RunPendingSummary,
    input: AcceptedAgentInput,
) -> WaitingRunContinueInput:
    feedback = normalize_feedback(
        waiting_run_id=waiting_run_id,
        sealed_state_digest_sha256=sealed_state_digest_sha256,
        pending=pending,
        submitted=(),
    )
    return WaitingRunContinueInput(**feedback.model_dump(mode="python"), input=input)


def _normalize_resolution(
    pending: PendingCallSummary,
    submitted: SubmittedPendingResolution | None,
) -> AcceptedPendingResolution:
    if submitted is None:
        outcome = (
            PendingResolutionOutcome.reject
            if pending.kind is PendingCallKind.approval
            else PendingResolutionOutcome.no_response
        )
        return AcceptedPendingResolution(call_id=pending.call_id, kind=pending.kind, outcome=outcome, result=None)
    if isinstance(submitted, ApprovePendingResolution | RejectPendingResolution):
        if pending.kind is not PendingCallKind.approval:
            raise ControlValidationError("feedback_action_invalid", "Feedback action does not match pending kind")
        return AcceptedPendingResolution(
            call_id=pending.call_id,
            kind=pending.kind,
            outcome=PendingResolutionOutcome(submitted.action.value),
            result=None,
        )
    if isinstance(submitted, CompletePendingResolution):
        if pending.kind is not PendingCallKind.client_tool:
            raise ControlValidationError("feedback_action_invalid", "Feedback action does not match pending kind")
        return AcceptedPendingResolution(
            call_id=pending.call_id,
            kind=pending.kind,
            outcome=PendingResolutionOutcome.complete,
            result=submitted.result,
        )
    if pending.kind is not PendingCallKind.user_input:
        raise ControlValidationError("feedback_action_invalid", "Feedback action does not match pending kind")
    return AcceptedPendingResolution(
        call_id=pending.call_id,
        kind=pending.kind,
        outcome=PendingResolutionOutcome.respond,
        result=submitted.response,
    )


class ThreadInboxKind(StrEnum):
    steer = "steer"
    async_subagent_result = "async_subagent_result"


class ThreadInboxStatus(StrEnum):
    pending = "pending"
    consumed = "consumed"
    superseded = "superseded"
    suppressed = "suppressed"
    expired = "expired"
    discarded = "discarded"


class InboxPayloadObjectRef(StrictModel):
    object_key: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    digest_sha256: Sha256Digest
    size_bytes: int = Field(ge=1)
    content_type: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    schema_version: Annotated[str, StringConstraints(min_length=1, max_length=32)]


class ThreadInboxEntry(StrictModel):
    id: ObjectId
    tenant_id: ObjectId
    thread_id: ThreadId
    kind: ThreadInboxKind
    delivery_sequence: int = Field(ge=1)
    accepted_against_run_id: ObjectId | None = None
    target_run_id: ObjectId | None = None
    source_waiting_run_id: ObjectId | None = None
    origin_run_id: ObjectId | None = None
    payload_schema_version: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    payload: JsonValue | None = None
    payload_object: InboxPayloadObjectRef | None = None
    status: ThreadInboxStatus
    consumed_by_run_id: ObjectId | None = None
    consumed_state_digest_sha256: Sha256Digest | None = None
    consumed_checkpoint_seq: int | None = Field(default=None, ge=0)
    expires_at: UtcDateTime | None = None
    created_at: UtcDateTime
    finalized_at: UtcDateTime | None = None

    @model_validator(mode="after")
    def shape_is_coherent(self) -> ThreadInboxEntry:
        _validate_inbox_payload(self)
        _validate_inbox_provenance(self)
        _validate_inbox_consumption(self)
        _validate_inbox_binding(self)
        return self


def _validate_inbox_payload(entry: ThreadInboxEntry) -> None:
    inline = "payload" in entry.model_fields_set
    if inline == (entry.payload_object is not None):
        raise ValueError("inbox entry requires exactly one payload representation")


def _validate_inbox_provenance(entry: ThreadInboxEntry) -> None:
    if entry.kind is ThreadInboxKind.steer:
        if entry.accepted_against_run_id is None or entry.origin_run_id is not None or entry.expires_at is not None:
            raise ValueError("steer inbox provenance is invalid")
        if entry.status not in {
            ThreadInboxStatus.pending,
            ThreadInboxStatus.consumed,
            ThreadInboxStatus.superseded,
        }:
            raise ValueError("steer inbox status is invalid")
    elif entry.accepted_against_run_id is not None or entry.origin_run_id is None:
        raise ValueError("async-result inbox provenance is invalid")


def _validate_inbox_consumption(entry: ThreadInboxEntry) -> None:
    consumed = entry.status is ThreadInboxStatus.consumed
    evidence = (
        entry.consumed_by_run_id,
        entry.consumed_state_digest_sha256,
        entry.consumed_checkpoint_seq,
    )
    if consumed != all(value is not None for value in evidence):
        raise ValueError("inbox consumption evidence is incomplete")
    if consumed and entry.consumed_by_run_id != entry.target_run_id:
        raise ValueError("consumed inbox target must equal the consuming Run")
    if (entry.status is not ThreadInboxStatus.pending) != (entry.finalized_at is not None):
        raise ValueError("inbox finalized_at must match terminal status")


def _validate_inbox_binding(entry: ThreadInboxEntry) -> None:
    if entry.status is not ThreadInboxStatus.pending:
        if entry.status is not ThreadInboxStatus.consumed and entry.target_run_id is not None:
            raise ValueError("terminal inbox disposition cannot retain an active target")
        return
    active = entry.target_run_id is not None and entry.source_waiting_run_id is None
    waiting = entry.target_run_id is None and entry.source_waiting_run_id is not None
    waiting_successor = (
        entry.target_run_id is not None
        and entry.source_waiting_run_id is not None
        and entry.target_run_id != entry.source_waiting_run_id
    )
    unbound_async = (
        entry.kind is ThreadInboxKind.async_subagent_result
        and entry.target_run_id is None
        and entry.source_waiting_run_id is None
    )
    if not (active or waiting or waiting_successor or unbound_async):
        raise ValueError("pending inbox binding is invalid")


class ThreadInboxCounter(StrictModel):
    tenant_id: ObjectId
    thread_id: ThreadId
    next_delivery_sequence: int = Field(ge=1)
    pending_count: int = Field(ge=0)
    pending_bytes: int = Field(ge=0)


class SteerReceipt(StrictModel):
    schema_version: Literal["1"] = "1"
    session_id: ObjectId
    thread_id: ThreadId
    run_id: ObjectId
    steer_id: ObjectId
    delivery_sequence: int = Field(ge=1)
    accepted_at: UtcDateTime


class SteerStatus(StrictModel):
    schema_version: Literal["1"] = "1"
    session_id: ObjectId
    thread_id: ThreadId
    accepted_against_run_id: ObjectId
    steer_id: ObjectId
    delivery_sequence: int = Field(ge=1)
    target_run_id: ObjectId | None
    source_waiting_run_id: ObjectId | None
    status: Literal[ThreadInboxStatus.pending, ThreadInboxStatus.consumed, ThreadInboxStatus.superseded]
    consumed_by_run_id: ObjectId | None
    consumed_state_digest_sha256: Sha256Digest | None
    consumed_checkpoint_seq: int | None
    created_at: UtcDateTime
    finalized_at: UtcDateTime | None


class InterruptRequest(StrictModel):
    expected_run_version: int = Field(ge=1)
    expected_thread_version: int = Field(ge=1)


class QueuedSubmissionState(StrEnum):
    queued = "queued"
    consumed = "consumed"
    failed = "failed"


class QueuedSubmissionFailure(StrictModel):
    code: BoundedKey
    message: BoundedName


class QueuedSubmission(StrictModel):
    queued_submission_id: ObjectId
    version: int = Field(ge=1)
    thread_id: ThreadId
    authority_principal: PrincipalRef
    position: int | None = Field(default=None, ge=1)
    submission: ThreadRunSubmissionIntent
    submission_digest_sha256: Sha256Digest
    state: QueuedSubmissionState
    consumed_run_id: ObjectId | None = None
    failure: QueuedSubmissionFailure | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime
    consumed_at: UtcDateTime | None = None
    failed_at: UtcDateTime | None = None

    @model_validator(mode="after")
    def state_is_derived(self) -> QueuedSubmission:
        consumed = self.consumed_run_id is not None and self.consumed_at is not None
        failed = self.failure is not None and self.failed_at is not None
        if (self.consumed_run_id is None) != (self.consumed_at is None):
            raise ValueError("queued submission consumption fields must be present together")
        if (self.failure is None) != (self.failed_at is None):
            raise ValueError("queued submission failure fields must be present together")
        if consumed and failed:
            raise ValueError("queued submission consumption and failure are mutually exclusive")
        expected_state = (
            QueuedSubmissionState.consumed
            if consumed
            else QueuedSubmissionState.failed
            if failed
            else QueuedSubmissionState.queued
        )
        if self.state is not expected_state:
            raise ValueError("queued submission state must derive from terminal evidence")
        if (self.position is not None) != (expected_state is QueuedSubmissionState.queued):
            raise ValueError("queued submission position must exist only while queued")
        if self.submission.digest_sha256() != self.submission_digest_sha256:
            raise ValueError("queued submission digest does not match its intent")
        return self


class QueuedSubmissionCollection(StrictModel):
    items: tuple[QueuedSubmission, ...]


class UpdateQueuedSubmissionRequest(StrictModel):
    expected_version: int = Field(ge=1)
    submission: ThreadRunSubmissionIntent


class ReorderQueuedSubmissionsRequest(StrictModel):
    expected_queue_version: int = Field(ge=0)
    queued_submission_ids: tuple[ObjectId, ...] = Field(max_length=256)

    @field_validator("queued_submission_ids")
    @classmethod
    def ids_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("queued submission IDs must be unique")
        return value


class ConsumeQueuedSubmissionRequest(StrictModel):
    expected_thread_version: int = Field(ge=1)
    expected_queue_version: int = Field(ge=0)


class QueuedSubmissionMutationReceipt(StrictModel):
    queued_submission: QueuedSubmission
    queue_version: int = Field(ge=0)


class RunAcceptanceReceipt(StrictModel):
    schema_version: Literal["1"] = "1"
    session_id: ObjectId
    thread_id: ThreadId
    thread_version: int = Field(ge=1)
    run_id: ObjectId
    run_version: int = Field(ge=1)
    status: Literal["accepted"] = "accepted"
    hook_subscription_id: ObjectId | None = None


class QueuedSubmissionConsumptionReceipt(StrictModel):
    outcome: Literal["run_accepted", "submission_failed"]
    queued_submission: QueuedSubmission
    queue_version: int = Field(ge=0)
    run: RunAcceptanceReceipt | None = None

    @model_validator(mode="after")
    def outcome_matches_resources(self) -> QueuedSubmissionConsumptionReceipt:
        accepted = self.outcome == "run_accepted"
        if accepted != (self.run is not None):
            raise ValueError("queue consumption outcome and Run receipt are inconsistent")
        expected_state = QueuedSubmissionState.consumed if accepted else QueuedSubmissionState.failed
        if self.queued_submission.state is not expected_state:
            raise ValueError("queue consumption outcome and queued submission are inconsistent")
        return self


class ThreadQueueMutationReceipt(StrictModel):
    thread_id: ThreadId
    queue_version: int = Field(ge=0)


class ThreadRunSubmissionReceipt(StrictModel):
    outcome: Literal["run_accepted", "queued"]
    run: RunAcceptanceReceipt | None = None
    queued_submission: QueuedSubmission | None = None
    queue_version: int = Field(ge=0)

    @model_validator(mode="after")
    def outcome_is_exclusive(self) -> ThreadRunSubmissionReceipt:
        accepted = self.outcome == "run_accepted"
        if accepted != (self.run is not None) or accepted == (self.queued_submission is not None):
            raise ValueError("submission receipt outcome and selected resource are inconsistent")
        return self


def new_thread_inbox_entry_id() -> str:
    return new_object_id("inb")


def new_queued_submission_id() -> str:
    return new_object_id("qsub")


def _canonical_bytes(value: StrictModel) -> bytes:
    try:
        return rfc8785.dumps(value.model_dump(mode="json", by_alias=True, exclude_none=True))
    except rfc8785.CanonicalizationError as error:
        raise ControlValidationError("value_not_canonicalizable", "Control value is not canonical JSON") from error


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class ControlValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


__all__ = [
    "AcceptedPendingResolution",
    "ApprovePendingResolution",
    "CompletePendingResolution",
    "ConsumeQueuedSubmissionRequest",
    "ControlValidationError",
    "InboxPayloadObjectRef",
    "InlineHookSubscriptionInput",
    "InterruptRequest",
    "PendingResolutionOutcome",
    "QueuedSubmission",
    "QueuedSubmissionCollection",
    "QueuedSubmissionConsumptionReceipt",
    "QueuedSubmissionFailure",
    "QueuedSubmissionMutationReceipt",
    "QueuedSubmissionState",
    "RejectPendingResolution",
    "ReorderQueuedSubmissionsRequest",
    "RespondPendingResolution",
    "RunAcceptanceReceipt",
    "SteerReceipt",
    "SteerStatus",
    "SubmittedPendingResolution",
    "ThreadInboxCounter",
    "ThreadInboxEntry",
    "ThreadInboxKind",
    "ThreadInboxStatus",
    "ThreadQueueMutationReceipt",
    "ThreadRunSubmissionIntent",
    "ThreadRunSubmissionReceipt",
    "ThreadRunSubmissionRequest",
    "UpdateQueuedSubmissionRequest",
    "WaitingResolutionDefaults",
    "WaitingRunContinueInput",
    "WaitingRunFeedback",
    "WaitingRunFeedbackRequest",
    "WebhookDestinationConfig",
    "new_queued_submission_id",
    "new_thread_inbox_entry_id",
    "normalize_feedback",
    "normalize_waiting_continue",
]
