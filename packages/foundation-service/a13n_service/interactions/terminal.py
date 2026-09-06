"""Database-backed terminal adapter for the process-local Attempt executor."""

from __future__ import annotations

import hashlib
import hmac

from a13n_harness import SafeFailure
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import short_session
from a13n_service.temporal import Clock, utc_now

from ._outcome_transitions import RunOutcomePreconditionChanged
from .attempts import (
    AttemptAuthorityError,
    AttemptContext,
    AttemptExecutionService,
    AttemptPreparationAccepted,
    read_attempt_authority,
)
from .domain import RunAttemptStatus, RunStatus
from .harness_results import RunTerminalDisposition, RunTerminalReceipt
from .models import RunAttemptRecord, RunRecord, ThreadRecord
from .objects import StoredRunState, run_state_key
from .outcomes import RunOutcomeService


class DatabaseRunTerminalCommitter:
    """Adapt existing fenced transactions without retaining ORM objects or sessions."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        outcomes: RunOutcomeService,
        execution: AttemptExecutionService,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._outcomes = outcomes
        self._execution = execution
        self._clock = clock

    async def commit_state_outcome(
        self,
        authority: AttemptContext,
        state: StoredRunState,
        *,
        preparation: AttemptPreparationAccepted | None = None,
    ) -> RunTerminalReceipt:
        if state.info.key != run_state_key(authority.tenant_id, authority.run_id):
            raise AttemptAuthorityError("The outcome state key does not belong to the Attempt")
        for retry in range(3):
            async with short_session(self._sessions) as database:
                replay = await self._sealed_receipt(database, authority, state)
                if replay is not None:
                    return replay
                _, _, thread = await read_attempt_authority(database, authority, self._clock())
                thread_version = thread.version
            try:
                receipt = await self._outcomes.commit_state_outcome(
                    authority,
                    state,
                    expected_thread_version=thread_version,
                    preparation=preparation,
                )
            except RunOutcomePreconditionChanged:
                if retry == 2:
                    raise
            else:
                assert receipt.attempt_version is not None
                return RunTerminalReceipt(
                    RunTerminalDisposition(receipt.run_status.value),
                    receipt.run_version,
                    receipt.attempt_version,
                    receipt.thread_version,
                )
        raise AssertionError("bounded outcome reconciliation did not return")

    async def commit_failure(self, authority: AttemptContext, failure: SafeFailure) -> RunTerminalReceipt:
        mutation = await self._execution.fail(authority, failure, retryable=False)
        return RunTerminalReceipt(
            RunTerminalDisposition.failed,
            mutation.run_version,
            mutation.attempt_version,
            mutation.thread_version,
        )

    async def reconcile_cancelled(self, authority: AttemptContext) -> RunTerminalReceipt:
        async with short_session(self._sessions) as database:
            run, attempt, thread = await self._read_records(database, authority)
            if run.status != RunStatus.cancelled.value or attempt.status != RunAttemptStatus.cancelled.value:
                raise AttemptAuthorityError("Local Harness cancellation has no matching durable cancellation")
            return RunTerminalReceipt(RunTerminalDisposition.cancelled, run.version, attempt.version, thread.version)

    async def _sealed_receipt(
        self, database: AsyncSession, authority: AttemptContext, state: StoredRunState
    ) -> RunTerminalReceipt | None:
        run, attempt, thread = await self._read_records(database, authority)
        if run.status not in {RunStatus.completed.value, RunStatus.waiting.value}:
            return None
        sealed = run.to_resource().sealed_state
        if (
            sealed is None
            or sealed.committed_by_run_attempt_id != authority.run_attempt_id
            or sealed.digest_sha256 != state.digest_sha256
            or sealed.size_bytes != state.info.size
            or state.writer_fence != authority.fence
            or state.info.key != run_state_key(authority.tenant_id, authority.run_id)
            or state.envelope.run_id != run.id
            or state.envelope.thread_id != thread.id
            or attempt.status != RunAttemptStatus.succeeded.value
        ):
            raise AttemptAuthorityError("A different state or Attempt already sealed the Run")
        return RunTerminalReceipt(RunTerminalDisposition(run.status), run.version, attempt.version, thread.version)

    @staticmethod
    async def _read_records(
        database: AsyncSession, authority: AttemptContext
    ) -> tuple[RunRecord, RunAttemptRecord, ThreadRecord]:
        row = (
            await database.execute(
                select(RunRecord, RunAttemptRecord, ThreadRecord)
                .join(
                    RunAttemptRecord,
                    (RunAttemptRecord.tenant_id == RunRecord.tenant_id) & (RunAttemptRecord.run_id == RunRecord.id),
                )
                .join(
                    ThreadRecord,
                    (ThreadRecord.tenant_id == RunRecord.tenant_id) & (ThreadRecord.id == RunRecord.thread_id),
                )
                .where(
                    RunRecord.tenant_id == authority.tenant_id,
                    RunRecord.id == authority.run_id,
                    ThreadRecord.id == authority.thread_id,
                    RunAttemptRecord.id == authority.run_attempt_id,
                    RunAttemptRecord.fence == authority.fence,
                    RunAttemptRecord.worker_id == authority.worker_id,
                    RunAttemptRecord.worker_generation == authority.worker_generation,
                    RunAttemptRecord.worker_build_id == authority.worker_build_id,
                    RunAttemptRecord.runtime_lock_digest == authority.runtime_lock_digest,
                )
            )
        ).one_or_none()
        if row is None:
            raise AttemptAuthorityError("The selected Attempt records are unavailable")
        if not hmac.compare_digest(
            row[1].lease_token_digest, hashlib.sha256(authority.lease_token.encode()).hexdigest()
        ):
            raise AttemptAuthorityError("The Attempt lease proof does not match")
        return row[0], row[1], row[2]
