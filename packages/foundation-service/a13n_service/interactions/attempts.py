"""Fenced mutations performed by the selected RunAttempt owner."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from a13n_harness import SafeFailure
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import short_session, transaction

from ._transitions import charge_attempt_usage, seal_failed_run, terminalize_attempt
from .domain import RecoveryUsage, RunAttemptStatus, RunAttemptYieldReason, RunStatus
from .models import RunAttemptRecord, RunRecord, ThreadRecord
from .objects import RunStateStore, StoredRunState
from .state import RunStateEnvelope


class AttemptAuthorityError(RuntimeError):
    """The caller no longer owns the selected unexpired Attempt lease."""


class AttemptMutationError(RuntimeError):
    """The requested mutation is incompatible with the current Attempt lifecycle."""


@dataclass(frozen=True, slots=True)
class AttemptAuthority:
    tenant_id: str
    run_id: str
    run_attempt_id: str
    fence: int
    lease_token: str = field(repr=False)
    worker_id: str
    worker_generation: str
    expected_run_version: int
    expected_attempt_version: int


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
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._clock = clock

    async def validate(self, authority: AttemptAuthority) -> AttemptMutationReceipt:
        """Revalidate current lease and fence without mutating durable state."""

        now = _utc(self._clock())
        async with short_session(self._sessions) as database:
            run, attempt, _ = await read_attempt_authority(database, authority, now)
            return _receipt(run, attempt)

    async def heartbeat(
        self,
        authority: AttemptAuthority,
        *,
        lease_duration: timedelta,
    ) -> AttemptMutationReceipt:
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        now = _utc(self._clock())
        async with transaction(self._sessions) as database:
            run, attempt, _ = await lock_attempt_authority(database, authority, now)
            attempt.heartbeat_at = now
            attempt.lease_expires_at = now + lease_duration
            attempt.updated_at = now
            attempt.version += 1
            return _receipt(run, attempt)

    async def enter_harness(
        self,
        authority: AttemptAuthority,
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
            or preparation.mutation.attempt_version != authority.expected_attempt_version
        ):
            raise AttemptMutationError("Harness entry requires the matching successful preparation decision")
        now = _utc(self._clock())
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
            return _receipt(run, attempt)

    async def commit_preparation_success(
        self,
        authority: AttemptAuthority,
    ) -> AttemptPreparationResult:
        """Commit the decision after exact state, dependency, and Principal preflight succeeds."""

        now = _utc(self._clock())
        async with transaction(self._sessions) as database:
            run, attempt, thread = await lock_attempt_authority(database, authority, now)
            failure = _active_budget_failure(run, attempt, now)
            if failure is None:
                return AttemptPreparationAccepted(
                    run_attempt_id=attempt.id,
                    fence=attempt.fence,
                    mutation=_receipt(run, attempt),
                )
            terminalize_attempt(attempt, RunAttemptStatus.failed, now, failure=failure)
            charge_attempt_usage(run, attempt)
            seal_failed_run(run, thread, failure, now)
            return AttemptPreparationRejected(
                run_attempt_id=attempt.id,
                fence=attempt.fence,
                mutation=_receipt(run, attempt),
                failure=failure,
            )

    async def add_usage(
        self,
        authority: AttemptAuthority,
        delta: RecoveryUsage,
    ) -> AttemptMutationReceipt:
        now = _utc(self._clock())
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

    async def increment_model_request(self, authority: AttemptAuthority) -> AttemptMutationReceipt:
        """Durably cross the model-request boundary before provider I/O begins."""

        return await self.add_usage(authority, RecoveryUsage(model_requests=1))

    async def publish_checkpoint(
        self,
        authority: AttemptAuthority,
        states: RunStateStore,
        current: StoredRunState,
        successor: RunStateEnvelope,
    ) -> StoredRunState:
        """Validate relational authority, then replace state outside the DB session."""

        now = _utc(self._clock())
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
        authority: AttemptAuthority,
        failure: SafeFailure,
        *,
        retryable: bool,
        retry_after: timedelta = timedelta(0),
    ) -> AttemptMutationReceipt:
        if retry_after < timedelta(0):
            raise ValueError("retry_after must not be negative")
        now = _utc(self._clock())
        async with transaction(self._sessions) as database:
            run, attempt, thread = await lock_attempt_authority(database, authority, now)
            terminalize_attempt(attempt, RunAttemptStatus.failed, now, failure=failure)
            charge_attempt_usage(run, attempt)
            run.current_run_attempt_id = None
            available_at = now + retry_after
            if retryable and _successor_budget_remains(run, available_at):
                run.available_at = available_at
                run.updated_at = now
                run.version += 1
            else:
                seal_failed_run(run, thread, failure, now)
            return _receipt(run, attempt)

    async def yield_attempt(
        self,
        authority: AttemptAuthority,
        reason: RunAttemptYieldReason,
    ) -> AttemptMutationReceipt:
        now = _utc(self._clock())
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
            return _receipt(run, attempt)


async def lock_attempt_authority(
    database: AsyncSession,
    authority: AttemptAuthority,
    now: datetime,
) -> tuple[RunRecord, RunAttemptRecord, ThreadRecord]:
    thread_id = await database.scalar(
        select(RunRecord.thread_id).where(
            RunRecord.tenant_id == authority.tenant_id,
            RunRecord.id == authority.run_id,
        )
    )
    if thread_id is None:
        raise AttemptAuthorityError("Run authority was not found")
    thread = await database.scalar(
        select(ThreadRecord)
        .where(ThreadRecord.tenant_id == authority.tenant_id, ThreadRecord.id == thread_id)
        .with_for_update()
    )
    run = await database.scalar(
        select(RunRecord)
        .where(RunRecord.tenant_id == authority.tenant_id, RunRecord.id == authority.run_id)
        .with_for_update()
    )
    attempt = await database.scalar(
        select(RunAttemptRecord)
        .where(
            RunAttemptRecord.tenant_id == authority.tenant_id,
            RunAttemptRecord.run_id == authority.run_id,
            RunAttemptRecord.id == authority.run_attempt_id,
        )
        .with_for_update()
    )
    _validate_authority(run, attempt, thread, authority, now)
    assert run is not None and attempt is not None and thread is not None
    return run, attempt, thread


async def read_attempt_authority(
    database: AsyncSession,
    authority: AttemptAuthority,
    now: datetime,
) -> tuple[RunRecord, RunAttemptRecord, ThreadRecord]:
    result = await database.execute(
        select(RunRecord, RunAttemptRecord, ThreadRecord)
        .join(
            RunAttemptRecord,
            (RunAttemptRecord.tenant_id == RunRecord.tenant_id)
            & (RunAttemptRecord.run_id == RunRecord.id)
            & (RunAttemptRecord.id == authority.run_attempt_id),
        )
        .join(
            ThreadRecord,
            (ThreadRecord.tenant_id == RunRecord.tenant_id) & (ThreadRecord.id == RunRecord.thread_id),
        )
        .where(RunRecord.tenant_id == authority.tenant_id, RunRecord.id == authority.run_id)
    )
    row = result.one_or_none()
    if row is None:
        raise AttemptAuthorityError("Attempt authority was not found")
    run, attempt, thread = row
    _validate_authority(run, attempt, thread, authority, now)
    return run, attempt, thread


def _validate_authority(
    run: RunRecord | None,
    attempt: RunAttemptRecord | None,
    thread: ThreadRecord | None,
    authority: AttemptAuthority,
    now: datetime,
) -> None:
    if run is None or attempt is None or thread is None:
        raise AttemptAuthorityError("Attempt authority was not found")
    token_matches = hmac.compare_digest(attempt.lease_token_digest, _token_digest(authority.lease_token))
    if (
        run.version != authority.expected_run_version
        or attempt.version != authority.expected_attempt_version
        or thread.current_run_id != run.id
        or run.status != RunStatus.running.value
        or run.current_run_attempt_id != attempt.id
        or attempt.status not in {RunAttemptStatus.leased.value, RunAttemptStatus.running.value}
        or attempt.fence != authority.fence
        or attempt.worker_id != authority.worker_id
        or attempt.worker_generation != authority.worker_generation
        or not token_matches
        or _utc(attempt.lease_expires_at) <= now
    ):
        raise AttemptAuthorityError("Attempt lease, fence, selection, or version is no longer authoritative")


def _successor_budget_remains(run: RunRecord, available_at: datetime) -> bool:
    if run.recovery_attempts_started >= run.max_recovery_attempts:
        return False
    if run.recovery_deadline_at is not None and available_at >= _utc(run.recovery_deadline_at):
        return False
    maximum = run.to_resource().recovery_budget.max_usage
    return maximum is None or maximum.permits(run.to_resource().usage_charged)


def _active_budget_failure(
    run: RunRecord,
    attempt: RunAttemptRecord,
    now: datetime,
) -> SafeFailure | None:
    if run.recovery_deadline_at is not None and now >= _utc(run.recovery_deadline_at):
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
        lease_expires_at=_utc(attempt.lease_expires_at),
    )


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "AttemptAuthority",
    "AttemptAuthorityError",
    "AttemptExecutionService",
    "AttemptMutationError",
    "AttemptMutationReceipt",
    "AttemptPreparationAccepted",
    "AttemptPreparationRejected",
    "AttemptPreparationResult",
]
