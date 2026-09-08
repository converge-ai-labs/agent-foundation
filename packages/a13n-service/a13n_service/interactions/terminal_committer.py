"""Production adapters for fenced Harness terminal decisions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta

from a13n_harness import SafeFailure
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import short_session
from a13n_service.temporal import utc_now

from .attempts import AttemptAuthorityError, AttemptContext, AttemptExecutionService, read_attempt_lease
from .domain import RunStatus
from .harness_results import RunTerminalDisposition, RunTerminalReceipt
from .models import RunAttemptRecord, RunRecord, ThreadRecord
from .objects import StoredRunState
from .outcomes import RunOutcomeService


class DatabaseRunTerminalCommitter:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        outcomes: RunOutcomeService,
        execution: AttemptExecutionService,
    ) -> None:
        self._sessions = sessions
        self._outcomes = outcomes
        self._execution = execution

    async def prepare_state_outcome(
        self, authority: AttemptContext, state: StoredRunState
    ) -> Callable[[AttemptContext], Awaitable[RunTerminalReceipt]]:
        verified = await self._outcomes.verify_state_outcome(authority, state)

        async def commit(current: AttemptContext) -> RunTerminalReceipt:
            async with short_session(self._sessions) as session:
                _, _, thread = await read_attempt_lease(session, current, utc_now())
                version = thread.version
            receipt = await self._outcomes.commit_verified_state_outcome(
                current, verified, expected_thread_version=version
            )
            assert receipt.attempt_version is not None
            return RunTerminalReceipt(
                RunTerminalDisposition(receipt.run_status.value),
                receipt.run_version,
                receipt.attempt_version,
                receipt.thread_version,
            )

        return commit

    async def commit_failure(self, authority: AttemptContext, failure: SafeFailure) -> RunTerminalReceipt:
        retryable = failure.code in {
            "attempt_dependency_unavailable",
            "model_provider_unavailable",
            "model_request_failed",
            "timeout",
            "object_store_unavailable",
            "environment_unavailable",
            "skill_materialization_unavailable",
        }
        await self._execution.fail(authority, failure, retryable=retryable, retry_after=timedelta(seconds=1))
        return await self._read_terminal(authority)

    async def reconcile_cancelled(self, authority: AttemptContext) -> RunTerminalReceipt:
        receipt = await self._read_terminal(authority)
        if receipt.disposition is not RunTerminalDisposition.cancelled:
            raise AttemptAuthorityError("Local cancellation does not prove durable cancellation")
        return receipt

    async def _read_terminal(self, authority: AttemptContext) -> RunTerminalReceipt:
        async with short_session(self._sessions) as session:
            run = await session.get(RunRecord, authority.run_id)
            attempt = await session.get(RunAttemptRecord, authority.run_attempt_id)
            thread = await session.get(ThreadRecord, authority.thread_id)
            if (
                run is None
                or attempt is None
                or thread is None
                or run.organization_id != authority.organization_id
                or attempt.run_id != run.id
                or run.thread_id != thread.id
            ):
                raise AttemptAuthorityError("Run terminal authority is unavailable")
            if run.status == RunStatus.running.value and attempt.status == "failed":
                return RunTerminalReceipt(RunTerminalDisposition.retrying, run.version, attempt.version, None)
            if run.status not in {"failed", "cancelled"}:
                raise AttemptAuthorityError("Run has no matching terminal decision")
            return RunTerminalReceipt(RunTerminalDisposition(run.status), run.version, attempt.version, thread.version)
