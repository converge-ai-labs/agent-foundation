"""Atomic RunAttempt, Run, and Thread outcome commits."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from a13n_harness import SafeFailure
from sqlalchemy import JSON, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import short_session, transaction

from ._transitions import charge_attempt_usage, terminalize_attempt
from .attempts import AttemptAuthority, AttemptMutationError, lock_attempt_authority, read_attempt_authority
from .domain import RunAttemptStatus, RunStatus
from .models import RunAttemptRecord, RunRecord, ThreadRecord
from .objects import RUN_STATE_CONTENT_TYPE, RunPayloadStore, StoredRunState
from .state import CompletedOutcomeCandidate, WaitingOutcomeCandidate


class RunOutcomeError(RuntimeError):
    """The prepared outcome is stale or inconsistent with relational authority."""


@dataclass(frozen=True, slots=True)
class RunOutcomeReceipt:
    run_status: RunStatus
    run_version: int
    attempt_version: int | None
    thread_version: int


class RunOutcomeService:
    """Seal state-first successful outcomes and interrupt-driven cancellation."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        payloads: RunPayloadStore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._payloads = payloads
        self._clock = clock

    async def commit_state_outcome(
        self,
        authority: AttemptAuthority,
        state: StoredRunState,
        *,
        expected_thread_version: int,
    ) -> RunOutcomeReceipt:
        """Adopt an already-published waiting or completed state candidate."""

        now = _utc(self._clock())
        _validate_candidate(state, authority)
        await self._verify_output_payload(authority, state, expected_thread_version, now)
        async with transaction(self._sessions) as database:
            run, attempt, thread = await lock_attempt_authority(database, authority, now)
            if thread.version != expected_thread_version:
                raise RunOutcomeError("Thread outcome precondition changed")
            _validate_candidate_scope(state, run, thread)
            if attempt.status != RunAttemptStatus.running.value:
                raise AttemptMutationError("a successful outcome requires Harness entry")
            envelope = state.envelope
            candidate = envelope.outcome_candidate
            if isinstance(candidate, WaitingOutcomeCandidate):
                _apply_waiting(run, candidate, now)
                status = RunStatus.waiting
            elif isinstance(candidate, CompletedOutcomeCandidate):
                _apply_completed(run, candidate, now)
                status = RunStatus.completed
            else:  # pragma: no cover - guarded by _validate_candidate
                raise RunOutcomeError("Run state does not contain a supported outcome candidate")

            terminalize_attempt(attempt, RunAttemptStatus.succeeded, now)
            charge_attempt_usage(run, attempt)
            run.current_run_attempt_id = None
            run.sealed_state_digest_sha256 = state.digest_sha256
            run.sealed_state_size_bytes = state.info.size
            run.sealed_state_content_type = state.info.content_type
            run.sealed_state_envelope_schema_version = envelope.schema_version
            run.sealed_state_harness_schema_version = envelope.harness_schema_version
            run.sealed_state_checkpoint_seq = envelope.checkpoint_seq
            run.sealed_state_committed_by_run_attempt_id = attempt.id
            run.sealed_at = now
            run.updated_at = now
            run.version += 1
            thread.head_run_id = run.id
            thread.version += 1
            thread.updated_at = now
            return RunOutcomeReceipt(status, run.version, attempt.version, thread.version)

    async def cancel(
        self,
        *,
        tenant_id: str,
        run_id: str,
        expected_run_version: int,
        expected_thread_version: int,
        failure: SafeFailure,
    ) -> RunOutcomeReceipt:
        """Seal an accepted or running Run without object-store I/O."""

        now = _utc(self._clock())
        async with transaction(self._sessions) as database:
            thread_id = await database.scalar(
                select(RunRecord.thread_id).where(RunRecord.tenant_id == tenant_id, RunRecord.id == run_id)
            )
            if thread_id is None:
                raise RunOutcomeError("Run was not found")
            thread = await database.scalar(
                select(ThreadRecord)
                .where(ThreadRecord.tenant_id == tenant_id, ThreadRecord.id == thread_id)
                .with_for_update()
            )
            run = await database.scalar(
                select(RunRecord).where(RunRecord.tenant_id == tenant_id, RunRecord.id == run_id).with_for_update()
            )
            if (
                run is None
                or thread is None
                or run.version != expected_run_version
                or thread.version != expected_thread_version
                or thread.current_run_id != run.id
                or run.status not in {RunStatus.accepted.value, RunStatus.running.value}
            ):
                raise RunOutcomeError("Run cancellation precondition changed")
            attempt: RunAttemptRecord | None = None
            if run.current_run_attempt_id is not None:
                attempt = await database.scalar(
                    select(RunAttemptRecord)
                    .where(
                        RunAttemptRecord.tenant_id == tenant_id,
                        RunAttemptRecord.run_id == run.id,
                        RunAttemptRecord.id == run.current_run_attempt_id,
                    )
                    .with_for_update()
                )
                if attempt is None or attempt.status not in {
                    RunAttemptStatus.leased.value,
                    RunAttemptStatus.running.value,
                }:
                    raise RunOutcomeError("selected RunAttempt cannot be cancelled")
                terminalize_attempt(attempt, RunAttemptStatus.cancelled, now, failure=failure)
                charge_attempt_usage(run, attempt)
            run.status = RunStatus.cancelled.value
            run.current_run_attempt_id = None
            run.failure_json = failure.model_dump(mode="json", by_alias=True)
            run.sealed_at = now
            run.updated_at = now
            run.version += 1
            thread.version += 1
            thread.updated_at = now
            return RunOutcomeReceipt(
                RunStatus.cancelled,
                run.version,
                None if attempt is None else attempt.version,
                thread.version,
            )

    async def _verify_output_payload(
        self,
        authority: AttemptAuthority,
        state: StoredRunState,
        expected_thread_version: int,
        now: datetime,
    ) -> None:
        candidate = state.envelope.outcome_candidate
        if not isinstance(candidate, CompletedOutcomeCandidate) or candidate.output_object is None:
            return
        async with short_session(self._sessions) as database:
            run, _, thread = await read_attempt_authority(database, authority, now)
            if thread.version != expected_thread_version:
                raise RunOutcomeError("Thread outcome precondition changed")
            _validate_candidate_scope(state, run, thread)
        await self._payloads.verify_reference(
            authority.tenant_id,
            authority.run_id,
            "output",
            candidate.output_object,
        )


def _validate_candidate(state: StoredRunState, authority: AttemptAuthority) -> None:
    envelope = state.envelope
    if (
        envelope.run_id != authority.run_id
        or envelope.last_checkpoint_run_attempt_id != authority.run_attempt_id
        or envelope.last_checkpoint_fence != authority.fence
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


def _validate_candidate_scope(state: StoredRunState, run: RunRecord, thread: ThreadRecord) -> None:
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


def _apply_waiting(run: RunRecord, candidate: WaitingOutcomeCandidate, now: datetime) -> None:
    run.status = RunStatus.waiting.value
    run.wait_reason = candidate.wait_reason.value
    run.pending_json = candidate.pending.model_dump(mode="json")
    run.waiting_at = now


def _apply_completed(run: RunRecord, candidate: CompletedOutcomeCandidate, now: datetime) -> None:
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


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = ["RunOutcomeError", "RunOutcomeReceipt", "RunOutcomeService"]
