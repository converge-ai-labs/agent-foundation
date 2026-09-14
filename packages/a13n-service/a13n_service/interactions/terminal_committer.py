"""Production adapters for fenced Harness terminal decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from a13n_harness import SafeFailure
from a13n_logging import get_logger
from anyio import fail_after
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import short_session

from .attempts import AttemptAuthorityError, AttemptContext, AttemptExecutionService
from .domain import RunStatus
from .harness_results import AttemptDisposition, AttemptOutcome
from .models import RunAttemptRecord, RunRecord, ThreadRecord
from .objects import StoredRunState
from .outcomes import RunOutcomeService, VerifiedRunOutcome
from .queue_completion import QueueCompletion
from .queue_handoff import QueueHandoffCommit
from .state import CompletedOutcomeCandidate

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _VerifiedQueueOutcome(VerifiedRunOutcome):
    commit_queue: QueueHandoffCommit


class DatabaseAttemptCommitter:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        outcomes: RunOutcomeService,
        execution: AttemptExecutionService,
        *,
        queue_completion: QueueCompletion | None = None,
    ) -> None:
        self._sessions = sessions
        self._outcomes = outcomes
        self._execution = execution
        self._queue_completion = queue_completion

    async def verify_state_outcome(self, authority: AttemptContext, state: StoredRunState) -> VerifiedRunOutcome:
        verified = await self._outcomes.verify_state_outcome(authority, state)
        if self._queue_completion is not None and isinstance(
            state.envelope.outcome_candidate, CompletedOutcomeCandidate
        ):
            try:
                with fail_after(min(5.0, authority.reconciliation_timeout.total_seconds() / 2)):
                    commit_queue = await self._queue_completion.prepare(authority, state)
                if commit_queue is not None:
                    return _VerifiedQueueOutcome(
                        verified.state, verified.organization_id, verified.run_id, verified.verifier, commit_queue
                    )
            except AttemptAuthorityError:
                raise
            except Exception as error:
                logger.info(
                    "queue_handoff_preparation_deferred",
                    extra={"run_id": authority.run_id, "error_type": type(error).__name__},
                )
        return verified

    async def commit_verified_state_outcome(
        self, authority: AttemptContext, verified: VerifiedRunOutcome
    ) -> AttemptOutcome:
        if isinstance(verified, _VerifiedQueueOutcome):
            try:
                with fail_after(min(5.0, authority.reconciliation_timeout.total_seconds() / 2)):
                    receipt = await verified.commit_queue()
            except Exception as error:
                # A lost COMMIT response must not turn an already completed source into failure.
                committed = await self._completed_handoff(authority, verified)
                if committed is not None:
                    return committed
                if isinstance(error, AttemptAuthorityError):
                    raise
                logger.info(
                    "queue_handoff_commit_deferred",
                    extra={"run_id": authority.run_id, "error_type": type(error).__name__},
                )
            else:
                logger.info(
                    "queue_handoff_committed",
                    extra={
                        "run_id": authority.run_id,
                        "queued_submission_id": receipt.queued_submission.queued_submission_id,
                        "outcome": receipt.outcome,
                    },
                )
                return AttemptOutcome(
                    AttemptDisposition.completed,
                    receipt.source_run_version,
                    receipt.source_attempt_version,
                    receipt.thread_version,
                )
        receipt = await self._outcomes.commit_verified_state_outcome(authority, verified)
        assert receipt.attempt_version is not None
        disposition = (
            AttemptDisposition.continuing
            if receipt.run_status is RunStatus.running
            else AttemptDisposition(receipt.run_status.value)
        )
        return AttemptOutcome(disposition, receipt.run_version, receipt.attempt_version, receipt.thread_version)

    async def _completed_handoff(
        self, authority: AttemptContext, verified: VerifiedRunOutcome
    ) -> AttemptOutcome | None:
        async with short_session(self._sessions) as database:
            run = await database.get(RunRecord, authority.run_id)
            attempt = await database.get(RunAttemptRecord, authority.run_attempt_id)
            thread = await database.get(ThreadRecord, authority.thread_id)
            if (
                run is not None
                and attempt is not None
                and thread is not None
                and run.organization_id == authority.organization_id
                and run.thread_id == thread.id
                and attempt.run_id == run.id
                and run.status == "completed"
                and attempt.status == "succeeded"
                and run.sealed_state_digest_sha256 == verified.state.digest_sha256
            ):
                return AttemptOutcome(AttemptDisposition.completed, run.version, attempt.version, thread.version)
        return None

    async def commit_failure(self, authority: AttemptContext, failure: SafeFailure) -> AttemptOutcome:
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

    async def reconcile_cancelled(self, authority: AttemptContext) -> AttemptOutcome:
        receipt = await self._read_terminal(authority)
        if receipt.disposition is not AttemptDisposition.cancelled:
            raise AttemptAuthorityError("Local cancellation does not prove durable cancellation")
        return receipt

    async def _read_terminal(self, authority: AttemptContext) -> AttemptOutcome:
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
                return AttemptOutcome(AttemptDisposition.retrying, run.version, attempt.version, None)
            if run.status not in {"failed", "cancelled"}:
                raise AttemptAuthorityError("Run has no matching terminal decision")
            return AttemptOutcome(AttemptDisposition(run.status), run.version, attempt.version, thread.version)
