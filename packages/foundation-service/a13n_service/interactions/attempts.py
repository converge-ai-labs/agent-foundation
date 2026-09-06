"""Fenced mutations performed by the selected RunAttempt owner."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from a13n_harness import SafeFailure
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.environments.usage import schedule_environment_maintenance
from a13n_service.lifecycle import new_mutation_id
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from ._transitions import charge_attempt_usage, seal_failed_run, terminalize_attempt
from .domain import RecoveryUsage, RunAttemptStatus, RunAttemptYieldReason, RunStatus
from .inbox_persistence import apply_run_outcome, lock_inbox_related_runs
from .lifecycle import LifecycleWriter
from .models import RunAttemptRecord, RunRecord, ThreadRecord
from .objects import RunStateStore, StoredRunState
from .state import RunStateEnvelope


class AttemptAuthorityError(RuntimeError):
    """The caller no longer owns the selected unexpired Attempt lease."""


class AttemptMutationError(RuntimeError):
    """The requested mutation is incompatible with the current Attempt lifecycle."""


@dataclass(frozen=True, slots=True)
class AttemptContext:
    """Claim-derived process-local correlation, authority, and fixed execution policy."""

    organization_id: str
    thread_id: str
    run_id: str
    run_attempt_id: str
    fence: int
    lease_token: str = field(repr=False)
    worker_id: str
    worker_generation: str
    worker_build_id: str
    runtime_lock_digest: str
    expected_run_version: int
    expected_attempt_version: int
    lease_expires_at: datetime
    lease_duration: timedelta
    renewal_interval: timedelta
    renewal_timeout: timedelta
    reconciliation_timeout: timedelta
    cleanup_timeout: timedelta

    def __post_init__(self) -> None:
        if self.fence < 1 or self.expected_run_version < 1 or self.expected_attempt_version < 1:
            raise ValueError("Attempt context versions and fence must be positive")
        if self.lease_duration <= timedelta(0):
            raise ValueError("Attempt lease duration must be positive")
        if not timedelta(0) < self.renewal_interval < self.lease_duration:
            raise ValueError("Attempt renewal interval must be positive and shorter than the lease")
        if not timedelta(0) < self.renewal_timeout < self.lease_duration:
            raise ValueError("Attempt renewal timeout must be positive and shorter than the lease")
        if self.reconciliation_timeout <= timedelta(0):
            raise ValueError("Attempt reconciliation timeout must be positive")
        if self.cleanup_timeout <= timedelta(0):
            raise ValueError("Attempt cleanup timeout must be positive")
        object.__setattr__(self, "lease_expires_at", assume_utc(self.lease_expires_at))


@dataclass(frozen=True, slots=True)
class AttemptMutationReceipt:
    run_version: int
    attempt_version: int
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class AttemptPreparationAccepted:
    run_attempt_id: str
    fence: int
    mutation: AttemptMutationReceipt


@dataclass(frozen=True, slots=True)
class AttemptPreparationRejected:
    run_attempt_id: str
    fence: int
    mutation: AttemptMutationReceipt
    failure: SafeFailure


type AttemptPreparationResult = AttemptPreparationAccepted | AttemptPreparationRejected


class AttemptExecutionService:
    """Apply heartbeat, Harness-entry, usage, checkpoint, failure, and yield CASes."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        lifecycle: LifecycleWriter,
        clock: Clock = utc_now,
    ) -> None:
        self._lifecycle = lifecycle
        self._sessions = sessions
        self._clock = clock

    async def validate(self, authority: AttemptContext) -> AttemptMutationReceipt:
        """Revalidate current lease and fence without mutating durable state."""

        now = assume_utc(self._clock())
        async with short_session(self._sessions) as database:
            run, attempt, _ = await read_attempt_authority(database, authority, now)
            return _receipt(run, attempt)

    async def heartbeat(
        self,
        authority: AttemptContext,
        *,
        lease_duration: timedelta,
    ) -> AttemptMutationReceipt:
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        now = assume_utc(self._clock())
        async with transaction(self._sessions) as database:
            run, attempt, _ = await lock_attempt_authority(database, authority, now)
            attempt.heartbeat_at = now
            attempt.lease_expires_at = now + lease_duration
            attempt.updated_at = now
            attempt.version += 1
            return _receipt(run, attempt)

    async def enter_harness(
        self,
        authority: AttemptContext,
        *,
        preparation: AttemptPreparationAccepted,
        harness_run_id: str,
    ) -> AttemptMutationReceipt:
        if not harness_run_id:
            raise ValueError("harness_run_id must not be empty")
        if (
            preparation.run_attempt_id != authority.run_attempt_id
            or preparation.fence != authority.fence
            or preparation.mutation.run_version != authority.expected_run_version
            or preparation.mutation.attempt_version > authority.expected_attempt_version
        ):
            raise AttemptMutationError("Harness entry requires the matching successful preparation decision")
        now = assume_utc(self._clock())
        async with transaction(self._sessions) as database:
            run, attempt, _ = await lock_attempt_authority(database, authority, now)
            if attempt.status != RunAttemptStatus.leased.value:
                raise AttemptMutationError("Harness entry requires a leased Attempt")
            attempt.status = RunAttemptStatus.running.value
            attempt.harness_run_id = harness_run_id
            attempt.started_at = now
            attempt.updated_at = now
            attempt.version += 1
            if run.started_at is None:
                run.started_at = now
            run.updated_at = now
            run.version += 1
            await self._lifecycle.append_run_attempt_lifecycle(
                database,
                run,
                attempt,
                "run_attempt.running",
                mutation_id=new_mutation_id(),
                occurred_at=now,
            )
            return _receipt(run, attempt)

    async def commit_preparation_success(
        self,
        authority: AttemptContext,
    ) -> AttemptPreparationResult:
        """Commit the decision after exact state, dependency, and Principal preflight succeeds."""

        now = assume_utc(self._clock())
        async with transaction(self._sessions) as database:
            run, attempt, thread = await lock_attempt_authority(
                database,
                authority,
                now,
                lock_inbox_origins=True,
            )
            failure = _active_budget_failure(run, attempt, now)
            if failure is None:
                return AttemptPreparationAccepted(
                    run_attempt_id=attempt.id,
                    fence=attempt.fence,
                    mutation=_receipt(run, attempt),
                )
            terminalize_attempt(attempt, RunAttemptStatus.failed, now, failure=failure)
            charge_attempt_usage(run, attempt)
            await schedule_environment_maintenance(database, run=run, now=now)
            await apply_run_outcome(database, run=run, outcome="failed", now=now)
            seal_failed_run(run, thread, failure, now)
            await self._lifecycle.append_run_with_attempt_lifecycle(
                database,
                run,
                "run.failed",
                attempt=attempt,
                attempt_event_type="run_attempt.failed",
                occurred_at=now,
                actor_type="worker",
                actor_id=attempt.worker_id,
            )
            return AttemptPreparationRejected(
                run_attempt_id=attempt.id,
                fence=attempt.fence,
                mutation=_receipt(run, attempt),
                failure=failure,
            )

    async def add_usage(
        self,
        authority: AttemptContext,
        delta: RecoveryUsage,
    ) -> AttemptMutationReceipt:
        now = assume_utc(self._clock())
        async with transaction(self._sessions) as database:
            run, attempt, _ = await lock_attempt_authority(database, authority, now)
            if attempt.status != RunAttemptStatus.running.value:
                raise AttemptMutationError("usage can be recorded only after Harness entry")
            usage = attempt.to_resource().usage.plus(delta)
            projected = run.to_resource().usage_charged.plus(usage)
            maximum = run.to_resource().recovery_budget.max_usage
            if maximum is not None and not maximum.permits(projected):
                raise AttemptMutationError("usage increment would exceed the accepted Run budget")
            attempt.usage_json = usage.model_dump(mode="json")
            attempt.updated_at = now
            attempt.version += 1
            return _receipt(run, attempt)

    async def increment_model_request(self, authority: AttemptContext) -> AttemptMutationReceipt:
        """Durably cross the model-request boundary before provider I/O begins."""

        return await self.add_usage(authority, RecoveryUsage(model_requests=1))

    async def publish_checkpoint(
        self,
        authority: AttemptContext,
        states: RunStateStore,
        current: StoredRunState,
        successor: RunStateEnvelope,
    ) -> StoredRunState:
        """Validate relational authority, then replace state outside the DB session."""

        now = assume_utc(self._clock())
        async with short_session(self._sessions) as database:
            await read_attempt_authority(database, authority, now)
        return await states.replace(
            current,
            successor,
            run_attempt_id=authority.run_attempt_id,
            fence=authority.fence,
        )

    async def fail(
        self,
        authority: AttemptContext,
        failure: SafeFailure,
        *,
        retryable: bool,
        retry_after: timedelta = timedelta(0),
    ) -> AttemptMutationReceipt:
        if retry_after < timedelta(0):
            raise ValueError("retry_after must not be negative")
        now = assume_utc(self._clock())
        async with transaction(self._sessions) as database:
            run, attempt, thread = await lock_attempt_authority(
                database,
                authority,
                now,
                lock_inbox_origins=True,
            )
            terminalize_attempt(attempt, RunAttemptStatus.failed, now, failure=failure)
            charge_attempt_usage(run, attempt)
            run.current_run_attempt_id = None
            available_at = now + retry_after
            mutation_id = new_mutation_id()
            if retryable and _successor_budget_remains(run, available_at):
                run.available_at = available_at
                run.updated_at = now
                run.version += 1
                await self._lifecycle.append_run_attempt_lifecycle(
                    database,
                    run,
                    attempt,
                    "run_attempt.failed",
                    mutation_id=mutation_id,
                    occurred_at=now,
                )
            else:
                await schedule_environment_maintenance(database, run=run, now=now)
                await apply_run_outcome(database, run=run, outcome="failed", now=now)
                seal_failed_run(run, thread, failure, now)
                await self._lifecycle.append_run_with_attempt_lifecycle(
                    database,
                    run,
                    "run.failed",
                    attempt=attempt,
                    attempt_event_type="run_attempt.failed",
                    mutation_id=mutation_id,
                    occurred_at=now,
                    actor_type="worker",
                    actor_id=attempt.worker_id,
                )
            return _receipt(run, attempt)

    async def yield_attempt(
        self,
        authority: AttemptContext,
        reason: RunAttemptYieldReason,
    ) -> AttemptMutationReceipt:
        now = assume_utc(self._clock())
        async with transaction(self._sessions) as database:
            run, attempt, _ = await lock_attempt_authority(database, authority, now)
            if run.handoffs_completed >= run.max_handoffs:
                raise AttemptMutationError("the Run handoff budget is exhausted")
            terminalize_attempt(attempt, RunAttemptStatus.yielded, now, yield_reason=reason)
            charge_attempt_usage(run, attempt)
            run.handoffs_completed += 1
            run.current_run_attempt_id = None
            run.available_at = now
            run.updated_at = now
            run.version += 1
            await self._lifecycle.append_run_attempt_lifecycle(
                database,
                run,
                attempt,
                "run_attempt.yielded",
                mutation_id=new_mutation_id(),
                occurred_at=now,
            )
            return _receipt(run, attempt)


async def lock_attempt_authority(
    database: AsyncSession,
    authority: AttemptContext,
    now: datetime,
    *,
    lock_inbox_origins: bool = False,
) -> tuple[RunRecord, RunAttemptRecord, ThreadRecord]:
    records = await lock_attempt_lease(database, authority, now, lock_inbox_origins=lock_inbox_origins)
    _validate_versions(records[0], records[1], authority)
    return records


async def lock_attempt_lease(
    database: AsyncSession,
    authority: AttemptContext,
    now: datetime,
    *,
    lock_inbox_origins: bool = False,
) -> tuple[RunRecord, RunAttemptRecord, ThreadRecord]:
    thread_id = await database.scalar(
        select(RunRecord.thread_id).where(
            RunRecord.organization_id == authority.organization_id,
            RunRecord.id == authority.run_id,
        )
    )
    if thread_id is None:
        raise AttemptAuthorityError("Run authority was not found")
    thread = await database.scalar(
        select(ThreadRecord)
        .where(ThreadRecord.organization_id == authority.organization_id, ThreadRecord.id == thread_id)
        .with_for_update()
    )
    if lock_inbox_origins:
        locked_runs = await lock_inbox_related_runs(
            database,
            organization_id=authority.organization_id,
            thread_id=thread_id,
            required_run_ids=(authority.run_id,),
        )
    else:
        locked_runs = tuple(
            (
                await database.scalars(
                    select(RunRecord)
                    .where(RunRecord.organization_id == authority.organization_id, RunRecord.id == authority.run_id)
                    .with_for_update()
                )
            ).all()
        )
    run = next((item for item in locked_runs if item.id == authority.run_id), None)
    attempt = await database.scalar(
        select(RunAttemptRecord)
        .where(
            RunAttemptRecord.organization_id == authority.organization_id,
            RunAttemptRecord.run_id == authority.run_id,
            RunAttemptRecord.id == authority.run_attempt_id,
        )
        .with_for_update()
    )
    _validate_lease(run, attempt, thread, authority, now)
    assert run is not None and attempt is not None and thread is not None
    return run, attempt, thread


async def read_attempt_authority(
    database: AsyncSession,
    authority: AttemptContext,
    now: datetime,
) -> tuple[RunRecord, RunAttemptRecord, ThreadRecord]:
    result = await database.execute(
        select(RunRecord, RunAttemptRecord, ThreadRecord)
        .join(
            RunAttemptRecord,
            (RunAttemptRecord.organization_id == RunRecord.organization_id)
            & (RunAttemptRecord.run_id == RunRecord.id)
            & (RunAttemptRecord.id == authority.run_attempt_id),
        )
        .join(
            ThreadRecord,
            (ThreadRecord.organization_id == RunRecord.organization_id) & (ThreadRecord.id == RunRecord.thread_id),
        )
        .where(RunRecord.organization_id == authority.organization_id, RunRecord.id == authority.run_id)
    )
    row = result.one_or_none()
    if row is None:
        raise AttemptAuthorityError("Attempt authority was not found")
    run, attempt, thread = row
    _validate_lease(run, attempt, thread, authority, now)
    _validate_versions(run, attempt, authority)
    return run, attempt, thread


def _validate_versions(run: RunRecord, attempt: RunAttemptRecord, authority: AttemptContext) -> None:
    if run.version != authority.expected_run_version or attempt.version != authority.expected_attempt_version:
        raise AttemptAuthorityError("Attempt mutation version is no longer authoritative")


def _validate_lease(
    run: RunRecord | None,
    attempt: RunAttemptRecord | None,
    thread: ThreadRecord | None,
    authority: AttemptContext,
    now: datetime,
) -> None:
    if run is None or attempt is None or thread is None:
        raise AttemptAuthorityError("Attempt authority was not found")
    token_matches = hmac.compare_digest(attempt.lease_token_digest, _token_digest(authority.lease_token))
    if (
        run.thread_id != authority.thread_id
        or thread.current_run_id != run.id
        or run.status != RunStatus.running.value
        or run.current_run_attempt_id != attempt.id
        or attempt.status not in {RunAttemptStatus.leased.value, RunAttemptStatus.running.value}
        or attempt.fence != authority.fence
        or attempt.worker_id != authority.worker_id
        or attempt.worker_generation != authority.worker_generation
        or attempt.worker_build_id != authority.worker_build_id
        or run.runtime_lock_digest != authority.runtime_lock_digest
        or attempt.runtime_lock_digest != authority.runtime_lock_digest
        or not token_matches
        or assume_utc(attempt.lease_expires_at) <= now
    ):
        raise AttemptAuthorityError("Attempt lease, fence, selection, or version is no longer authoritative")


def _successor_budget_remains(run: RunRecord, available_at: datetime) -> bool:
    if run.recovery_attempts_started >= run.max_recovery_attempts:
        return False
    if run.recovery_deadline_at is not None and available_at >= assume_utc(run.recovery_deadline_at):
        return False
    maximum = run.to_resource().recovery_budget.max_usage
    return maximum is None or maximum.permits(run.to_resource().usage_charged)


def _active_budget_failure(
    run: RunRecord,
    attempt: RunAttemptRecord,
    now: datetime,
) -> SafeFailure | None:
    if run.recovery_deadline_at is not None and now >= assume_utc(run.recovery_deadline_at):
        return SafeFailure(
            code="recovery_deadline_exhausted",
            message="The Run recovery deadline was exhausted during preparation.",
        )
    maximum = run.to_resource().recovery_budget.max_usage
    projected = run.to_resource().usage_charged.plus(attempt.to_resource().usage)
    if maximum is not None and not maximum.permits(projected):
        return SafeFailure(
            code="recovery_usage_exhausted",
            message="The Run recovery usage budget was exhausted during preparation.",
        )
    return None


def _receipt(run: RunRecord, attempt: RunAttemptRecord) -> AttemptMutationReceipt:
    return AttemptMutationReceipt(
        run_version=run.version,
        attempt_version=attempt.version,
        lease_expires_at=assume_utc(attempt.lease_expires_at),
    )


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


__all__ = [
    "AttemptAuthorityError",
    "AttemptContext",
    "AttemptExecutionService",
    "AttemptMutationError",
    "AttemptMutationReceipt",
    "AttemptPreparationAccepted",
    "AttemptPreparationRejected",
    "AttemptPreparationResult",
]
