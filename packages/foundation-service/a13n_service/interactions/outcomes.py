"""Atomic RunAttempt, Run, and Thread outcome commits."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from a13n_harness import SafeFailure
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.environments.usage import schedule_environment_maintenance
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from ._outcome_transitions import (
    RunOutcomeError,
    apply_completed_outcome,
    apply_waiting_outcome,
    select_sealed_state,
    validate_outcome_candidate,
    validate_outcome_candidate_scope,
)
from ._transitions import charge_attempt_usage, terminalize_attempt
from .attempts import (
    AttemptContext,
    AttemptMutationError,
    AttemptPreparationAccepted,
    lock_attempt_authority,
    read_attempt_authority,
)
from .domain import RunAttemptStatus, RunStatus
from .inbox import ThreadControlSignalPublisher
from .inbox_persistence import apply_run_outcome, lock_inbox_related_runs
from .lifecycle import append_run_lifecycle, append_run_with_attempt_lifecycle
from .models import RunAttemptRecord, RunRecord, ThreadRecord
from .objects import RunPayloadStore, StoredRunState
from .state import CompletedOutcomeCandidate, WaitingOutcomeCandidate

logger = logging.getLogger("a13n_service.interactions.outcomes")


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
        control_signals: ThreadControlSignalPublisher | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._payloads = payloads
        self._control_signals = control_signals
        self._clock = clock

    async def commit_state_outcome(
        self,
        authority: AttemptContext,
        state: StoredRunState,
        *,
        expected_thread_version: int,
        preparation: AttemptPreparationAccepted | None = None,
    ) -> RunOutcomeReceipt:
        """Adopt an already-published waiting or completed state candidate."""

        now = assume_utc(self._clock())
        validate_outcome_candidate(state, authority)
        await self._verify_output_payload(authority, state, expected_thread_version, now)
        async with transaction(self._sessions) as database:
            run, attempt, thread = await lock_attempt_authority(
                database,
                authority,
                now,
                lock_inbox_origins=True,
            )
            if thread.version != expected_thread_version:
                raise RunOutcomeError("Thread outcome precondition changed")
            validate_outcome_candidate_scope(state, run, thread)
            if attempt.status != RunAttemptStatus.running.value:
                if (
                    attempt.status != RunAttemptStatus.leased.value
                    or state.envelope.last_checkpoint_fence >= authority.fence
                    or preparation is None
                    or preparation.run_attempt_id != authority.run_attempt_id
                    or preparation.fence != authority.fence
                    or preparation.mutation.run_version != authority.expected_run_version
                    or preparation.mutation.attempt_version > authority.expected_attempt_version
                ):
                    raise AttemptMutationError(
                        "outcome adoption requires a prior checkpoint and matching successful preparation"
                    )
            envelope = state.envelope
            candidate = envelope.outcome_candidate
            if isinstance(candidate, WaitingOutcomeCandidate):
                await schedule_environment_maintenance(database, run=run, now=now)
                await apply_run_outcome(database, run=run, outcome="waiting", state=state, now=now)
                apply_waiting_outcome(run, candidate, now)
                status = RunStatus.waiting
            elif isinstance(candidate, CompletedOutcomeCandidate):
                await schedule_environment_maintenance(database, run=run, now=now)
                await apply_run_outcome(database, run=run, outcome="completed", state=state, now=now)
                apply_completed_outcome(run, candidate, now)
                status = RunStatus.completed
            else:  # pragma: no cover - guarded by validate_outcome_candidate
                raise RunOutcomeError("Run state does not contain a supported outcome candidate")

            terminalize_attempt(attempt, RunAttemptStatus.succeeded, now)
            charge_attempt_usage(run, attempt)
            run.current_run_attempt_id = None
            select_sealed_state(run, run_attempt_id=attempt.id, state=state, now=now)
            run.updated_at = now
            run.version += 1
            thread.head_run_id = run.id
            thread.version += 1
            thread.updated_at = now
            await append_run_with_attempt_lifecycle(
                database,
                run,
                "run.waiting" if status is RunStatus.waiting else "run.completed",
                attempt=attempt,
                attempt_event_type="run_attempt.succeeded",
                occurred_at=now,
                actor_type="worker",
                actor_id=attempt.worker_id,
            )
            return RunOutcomeReceipt(status, run.version, attempt.version, thread.version)

    async def cancel(
        self,
        *,
        tenant_id: str,
        run_id: str,
        expected_run_version: int,
        expected_thread_version: int,
        failure: SafeFailure,
        final_validator: Callable[[AsyncSession], Awaitable[None]] | None = None,
        transaction_hook: Callable[[AsyncSession], Awaitable[None]] | None = None,
    ) -> RunOutcomeReceipt:
        """Seal an accepted or running Run without object-store I/O."""

        now = assume_utc(self._clock())
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
            locked_runs = await lock_inbox_related_runs(
                database,
                tenant_id=tenant_id,
                thread_id=thread_id,
                required_run_ids=(run_id,),
            )
            run = next((item for item in locked_runs if item.id == run_id), None)
            if (
                run is None
                or thread is None
                or run.version != expected_run_version
                or thread.version != expected_thread_version
                or thread.current_run_id != run.id
                or run.status not in {RunStatus.accepted.value, RunStatus.running.value}
            ):
                raise RunOutcomeError("Run cancellation precondition changed")
            if final_validator is not None:
                await final_validator(database)
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
            await schedule_environment_maintenance(database, run=run, now=now)
            await apply_run_outcome(database, run=run, outcome="cancelled", now=now)
            run.status = RunStatus.cancelled.value
            run.current_run_attempt_id = None
            run.failure_json = failure.model_dump(mode="json", by_alias=True)
            run.sealed_at = now
            run.updated_at = now
            run.version += 1
            thread.version += 1
            thread.updated_at = now
            if attempt is None:
                await append_run_lifecycle(
                    database,
                    run,
                    "run.cancelled",
                    occurred_at=now,
                    actor_type="system",
                    actor_id=None,
                )
            else:
                await append_run_with_attempt_lifecycle(
                    database,
                    run,
                    "run.cancelled",
                    attempt=attempt,
                    attempt_event_type="run_attempt.cancelled",
                    occurred_at=now,
                    actor_type="system",
                    actor_id=None,
                )
            receipt = RunOutcomeReceipt(
                RunStatus.cancelled,
                run.version,
                None if attempt is None else attempt.version,
                thread.version,
            )
            if transaction_hook is not None:
                await transaction_hook(database)
            cancelled_thread_id = thread.id
        await self._best_effort_signal(tenant_id=tenant_id, thread_id=cancelled_thread_id)
        return receipt

    async def _verify_output_payload(
        self,
        authority: AttemptContext,
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
            validate_outcome_candidate_scope(state, run, thread)
        await self._payloads.verify_reference(
            authority.tenant_id,
            authority.run_id,
            "output",
            candidate.output_object,
        )

    async def _best_effort_signal(self, *, tenant_id: str, thread_id: str) -> None:
        if self._control_signals is None:
            return
        try:
            await self._control_signals.publish(tenant_id=tenant_id, thread_id=thread_id)
        except Exception:
            logger.warning(
                "thread_control_signal_failed",
                extra={"event": "thread_control_signal_failed", "thread_id": thread_id},
                exc_info=True,
            )


__all__ = [
    "RunOutcomeError",
    "RunOutcomeReceipt",
    "RunOutcomeService",
]
