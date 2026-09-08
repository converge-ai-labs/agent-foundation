"""Complete immutable Run state and payload envelopes."""

from __future__ import annotations

from typing import Annotated, Literal

from a13n_harness import HarnessState
from a13n_harness.toolsets.interaction import ASK_USER_QUESTION_TOOL_NAME
from pydantic import Field, JsonValue, TypeAdapter, field_validator, model_serializer, model_validator
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.usage import UsageLimits

from a13n_service.agents.domain import EffectiveAgentConfig

from .domain import (
    BoundedName,
    JsonObject,
    ObjectId,
    PendingCallKind,
    RunPayloadObjectRef,
    RunPendingSummary,
    RunWaitReason,
    SchemaVersion,
    StrictModel,
    ThreadId,
)

_DEFERRED_REQUESTS_ADAPTER = TypeAdapter(DeferredToolRequests)


class DeferredContinuationState(StrictModel):
    schema_version: Literal["1"] = "1"
    requests: JsonObject
    effective_client_tool_surface: JsonValue | None = None
    effective_surface_digest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def surface_and_digest_are_paired(self) -> DeferredContinuationState:
        if (self.effective_client_tool_surface is None) != (self.effective_surface_digest_sha256 is None):
            raise ValueError("effective client-tool surface and digest must be present together")
        return self


class InboxReceipt(StrictModel):
    inbox_entry_id: ObjectId
    kind: BoundedName


class HostContinuationState(StrictModel):
    schema_version: Literal["2"] = "2"
    deferred: DeferredContinuationState | None = None
    inbox_receipts: tuple[InboxReceipt, ...] = Field(default=(), max_length=1024)

    @field_validator("inbox_receipts")
    @classmethod
    def receipts_are_unique(cls, value: tuple[InboxReceipt, ...]) -> tuple[InboxReceipt, ...]:
        identities = tuple(receipt.inbox_entry_id for receipt in value)
        if len(identities) != len(set(identities)):
            raise ValueError("consumed Thread inbox receipt IDs must be unique")
        return value


class WaitingOutcomeCandidate(StrictModel):
    outcome: Literal["waiting"] = "waiting"
    wait_reason: RunWaitReason
    pending: RunPendingSummary
    output: None = None
    output_object: None = None
    output_text: None = None


class CompletedOutcomeCandidate(StrictModel):
    outcome: Literal["completed"] = "completed"
    wait_reason: None = None
    pending: None = None
    output: JsonValue | None = None
    output_object: RunPayloadObjectRef | None = None
    output_text: Annotated[str, Field(max_length=65536)] | None = None

    @model_validator(mode="after")
    def output_representation_is_exclusive(self) -> CompletedOutcomeCandidate:
        inline = "output" in self.model_fields_set
        if inline == (self.output_object is not None):
            raise ValueError("completed outcome requires exactly one inline or object output")
        return self

    @model_serializer(mode="wrap")
    def omit_unselected_output(self, handler):
        data = handler(self)
        if "output" not in self.model_fields_set:
            data.pop("output", None)
        if self.output_object is None:
            data.pop("output_object", None)
        return data


RunStateOutcomeCandidate = Annotated[
    WaitingOutcomeCandidate | CompletedOutcomeCandidate,
    Field(discriminator="outcome"),
]


class RunCheckpoint(StrictModel):
    schema_version: Literal["2"] = "2"
    run_id: ObjectId
    thread_id: ThreadId
    checkpoint_seq: int = Field(ge=0)
    checkpoint_kind: Literal["initial", "progress", "waiting", "completed"]
    last_checkpoint_run_attempt_id: ObjectId | None = None
    last_checkpoint_fence: int = Field(ge=0)
    agent_id: ObjectId
    agent_revision_id: ObjectId
    effective_agent_config: EffectiveAgentConfig
    usage_limits: UsageLimits | None = None
    runtime_lock_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    harness_schema_version: SchemaVersion
    harness: HarnessState
    host: HostContinuationState = Field(default_factory=HostContinuationState)
    outcome_candidate: RunStateOutcomeCandidate | None = None

    @property
    def initial_input_applied(self) -> bool:
        return self.checkpoint_seq > 0

    @model_validator(mode="after")
    def checkpoint_is_coherent(self) -> RunCheckpoint:
        if self.thread_id != self.harness.thread_id:
            raise ValueError("Run state and Harness Thread identities must match")
        if self.harness_schema_version != self.harness.schema_version:
            raise ValueError("declared Harness schema version must match Harness state")
        if self.runtime_lock_digest != self.effective_agent_config.runtime_lock_digest:
            raise ValueError("Run state Runtime lock must match effective Agent configuration")
        attempt_present = self.last_checkpoint_run_attempt_id is not None
        if attempt_present != (self.last_checkpoint_fence > 0):
            raise ValueError("checkpoint Attempt identity and positive fencing number must be present together")
        if self.checkpoint_kind == "initial":
            if self.checkpoint_seq != 0 or attempt_present or self.outcome_candidate is not None:
                raise ValueError("initial state requires sequence zero, pending input, and no Attempt or outcome")
        else:
            if self.checkpoint_seq < 1 or not attempt_present:
                raise ValueError("non-initial state requires an applied input and fenced positive checkpoint")
        if self.checkpoint_kind == "progress":
            if self.outcome_candidate is not None:
                raise ValueError("progress checkpoint cannot carry an outcome")
        elif self.checkpoint_kind == "waiting":
            if not isinstance(self.outcome_candidate, WaitingOutcomeCandidate) or self.host.deferred is None:
                raise ValueError("waiting checkpoint requires matching outcome and deferred continuation")
            _validate_waiting_projection(self.host.deferred, self.outcome_candidate)
        elif self.checkpoint_kind == "completed":
            if not isinstance(self.outcome_candidate, CompletedOutcomeCandidate) or self.host.deferred is not None:
                raise ValueError("completed checkpoint requires matching outcome and no deferred continuation")
        return self


def _validate_waiting_projection(
    deferred: DeferredContinuationState,
    candidate: WaitingOutcomeCandidate,
) -> None:
    try:
        requests = _DEFERRED_REQUESTS_ADAPTER.validate_python(deferred.requests)
    except (TypeError, ValueError) as error:
        raise ValueError("waiting state contains invalid native deferred requests") from error
    native: dict[str, tuple[PendingCallKind, str]] = {}
    for request in requests.approvals:
        _add_deferred_request(native, request.tool_call_id, PendingCallKind.approval, request.tool_name)
    for request in requests.calls:
        kind = (
            PendingCallKind.user_input
            if request.tool_name == ASK_USER_QUESTION_TOOL_NAME
            else PendingCallKind.client_tool
        )
        _add_deferred_request(native, request.tool_call_id, kind, request.tool_name)
    projected = {call.call_id: (call.kind, call.tool_name) for call in candidate.pending.calls}
    if set(projected) != set(native):
        raise ValueError("waiting pending summary call IDs must equal native deferred requests")
    for call_id, (kind, tool_name) in projected.items():
        native_kind, native_tool_name = native[call_id]
        if kind is not native_kind or (tool_name is not None and tool_name != native_tool_name):
            raise ValueError("waiting pending summary must preserve native request kind and tool name")
    kinds = {kind for kind, _ in native.values()}
    if PendingCallKind.client_tool in kinds and deferred.effective_client_tool_surface is None:
        raise ValueError("client-tool waiting state requires its frozen effective client-tool surface")
    expected_reason = RunWaitReason.multiple if len(kinds) > 1 else RunWaitReason(next(iter(kinds)).value)
    if candidate.wait_reason is not expected_reason:
        raise ValueError("waiting reason must match the native deferred request kinds")


def _add_deferred_request(
    requests: dict[str, tuple[PendingCallKind, str]],
    call_id: str,
    kind: PendingCallKind,
    tool_name: str,
) -> None:
    if not call_id or call_id in requests:
        raise ValueError("native deferred request call IDs must be non-empty and unique")
    requests[call_id] = (kind, tool_name)


class RunPayloadEnvelope(StrictModel):
    schema_version: Literal["1"] = "1"
    run_id: ObjectId
    payload_kind: Literal["input", "output"]
    payload_schema_version: SchemaVersion
    payload: JsonValue


def validate_state_successor(
    previous: RunCheckpoint,
    successor: RunCheckpoint,
    *,
    run_attempt_id: str,
    attempt_number: int,
) -> None:
    """Validate one same-Run semantic checkpoint replacement."""

    if previous.outcome_candidate is not None:
        added_input = set(previous.host.inbox_receipts) < set(successor.host.inbox_receipts)
        continuing = (
            isinstance(previous.outcome_candidate, CompletedOutcomeCandidate)
            and successor.checkpoint_kind == "progress"
        )
        repaired = successor.model_copy(
            update={
                "checkpoint_seq": previous.checkpoint_seq,
                "last_checkpoint_run_attempt_id": previous.last_checkpoint_run_attempt_id,
                "last_checkpoint_fence": previous.last_checkpoint_fence,
                "host": successor.host.model_copy(update={"inbox_receipts": previous.host.inbox_receipts}),
            }
        )
        if not added_input or (not continuing and repaired != previous):
            raise ValueError("Run outcome candidate permits receipt repair or completed-input continuation")
    immutable_pairs = (
        ("run_id", previous.run_id, successor.run_id),
        ("thread_id", previous.thread_id, successor.thread_id),
        ("agent_id", previous.agent_id, successor.agent_id),
        ("agent_revision_id", previous.agent_revision_id, successor.agent_revision_id),
        ("runtime_lock_digest", previous.runtime_lock_digest, successor.runtime_lock_digest),
        ("harness_schema_version", previous.harness_schema_version, successor.harness_schema_version),
        ("effective_agent_config", previous.effective_agent_config, successor.effective_agent_config),
        ("usage_limits", previous.usage_limits, successor.usage_limits),
    )
    changed = [name for name, old, new in immutable_pairs if old != new]
    if changed:
        raise ValueError(f"Run state immutable fields changed: {', '.join(changed)}")
    if successor.checkpoint_seq != previous.checkpoint_seq + 1:
        raise ValueError("Run checkpoint sequence must increase by exactly one")
    if successor.last_checkpoint_run_attempt_id != run_attempt_id or successor.last_checkpoint_fence != attempt_number:
        raise ValueError("Run checkpoint must name the current Attempt and fencing number")
    if attempt_number < previous.last_checkpoint_fence:
        raise ValueError("Run checkpoint fence cannot move backwards")
    previous_receipts = set(previous.host.inbox_receipts)
    successor_receipts = set(successor.host.inbox_receipts)
    if not previous_receipts <= successor_receipts:
        raise ValueError("same-Run consumed inbox receipts cannot disappear")


__all__ = [
    "CompletedOutcomeCandidate",
    "DeferredContinuationState",
    "HostContinuationState",
    "InboxReceipt",
    "RunCheckpoint",
    "RunPayloadEnvelope",
    "RunStateOutcomeCandidate",
    "WaitingOutcomeCandidate",
    "validate_state_successor",
]
