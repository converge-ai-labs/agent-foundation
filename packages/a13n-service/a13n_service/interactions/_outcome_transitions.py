"""Canonical state-first outcome validation and Run projection."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON

from .domain import RunStatus
from .models import RunRecord, ThreadRecord
from .objects import RUN_STATE_CONTENT_TYPE, StoredRunState
from .state import CompletedOutcomeCandidate, WaitingOutcomeCandidate

if TYPE_CHECKING:
    from .attempts import AttemptContext


class RunOutcomeError(RuntimeError):
    """The prepared outcome is stale or inconsistent with relational authority."""


def validate_outcome_candidate(state: StoredRunState, authority: AttemptContext) -> None:
    envelope = state.envelope
    if (
        envelope.run_id != authority.run_id
        or envelope.last_checkpoint_fence > authority.fence
        or (
            envelope.last_checkpoint_fence == authority.fence
            and envelope.last_checkpoint_run_attempt_id != authority.run_attempt_id
        )
        or state.writer_fence != authority.fence
        or state.info.content_type != RUN_STATE_CONTENT_TYPE
        or envelope.checkpoint_seq < 1
    ):
        raise RunOutcomeError("state candidate does not match the current fenced Attempt")
    candidate = envelope.outcome_candidate
    if envelope.checkpoint_kind == "waiting" and isinstance(candidate, WaitingOutcomeCandidate):
        return
    if envelope.checkpoint_kind == "completed" and isinstance(candidate, CompletedOutcomeCandidate):
        return
    raise RunOutcomeError("state checkpoint kind and outcome candidate do not match")


def validate_outcome_candidate_scope(
    state: StoredRunState,
    run: RunRecord,
    thread: ThreadRecord,
) -> None:
    envelope = state.envelope
    if (
        envelope.thread_id != thread.id
        or envelope.thread_id != run.thread_id
        or envelope.agent_id != run.agent_id
        or envelope.agent_revision_id != run.agent_revision_id
        or envelope.effective_agent_config.content_digest != run.effective_agent_config_digest
        or envelope.runtime_lock_digest != run.runtime_lock_digest
    ):
        raise RunOutcomeError("state candidate scope or frozen execution selection does not match the Run")


def apply_waiting_outcome(run: RunRecord, candidate: WaitingOutcomeCandidate, now: datetime) -> None:
    run.status = RunStatus.waiting.value
    run.wait_reason = candidate.wait_reason.value
    run.pending_json = candidate.pending.model_dump(mode="json")
    run.waiting_at = now


def apply_completed_outcome(run: RunRecord, candidate: CompletedOutcomeCandidate, now: datetime) -> None:
    run.status = RunStatus.completed.value
    run.output_json = None
    run.output_object_key = None
    run.output_object_digest_sha256 = None
    run.output_object_size_bytes = None
    run.output_object_content_type = None
    run.output_object_schema_version = None
    if "output" in candidate.model_fields_set:
        run.output_json = JSON.NULL if candidate.output is None else candidate.output
    else:
        assert candidate.output_object is not None
        reference = candidate.output_object
        run.output_object_key = reference.object_key
        run.output_object_digest_sha256 = reference.digest_sha256
        run.output_object_size_bytes = reference.size_bytes
        run.output_object_content_type = reference.content_type
        run.output_object_schema_version = reference.schema_version
    run.output_text = candidate.output_text
    run.completed_at = now


def select_sealed_state(
    run: RunRecord,
    *,
    run_attempt_id: str,
    state: StoredRunState,
    now: datetime,
) -> None:
    envelope = state.envelope
    run.sealed_state_digest_sha256 = state.digest_sha256
    run.sealed_state_size_bytes = state.info.size
    run.sealed_state_content_type = state.info.content_type
    run.sealed_state_envelope_schema_version = envelope.schema_version
    run.sealed_state_harness_schema_version = envelope.harness_schema_version
    run.sealed_state_checkpoint_seq = envelope.checkpoint_seq
    run.sealed_state_committed_by_run_attempt_id = run_attempt_id
    run.sealed_at = now


__all__ = [
    "RunOutcomeError",
    "apply_completed_outcome",
    "apply_waiting_outcome",
    "select_sealed_state",
    "validate_outcome_candidate",
    "validate_outcome_candidate_scope",
]
