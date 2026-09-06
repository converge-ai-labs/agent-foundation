"""Relational RunAttempt candidate selection, claim, and lease takeover."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from a13n_harness import SafeFailure
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from a13n_service.environments.identity import local_backend_eligible
from a13n_service.environments.models import EnvironmentProviderRecord, EnvironmentRecord
from a13n_service.environments.usage import schedule_environment_maintenance
from a13n_service.lifecycle import new_mutation_id
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from ._transitions import charge_attempt_usage, seal_failed_run, terminalize_attempt
from .domain import RecoveryUsage, Run, RunAttempt, RunAttemptStatus, RunStatus, new_run_attempt_id
from .inbox_persistence import apply_run_outcome, lock_inbox_related_runs
from .lifecycle import LifecycleWriter
from .models import RunAttemptRecord, RunRecord, SessionRecord, ThreadRecord
from .records import run_attempt_record


class AttemptSchedulingError(RuntimeError):
    """The claimant supplied invalid or incompatible scheduling authority."""


@dataclass(frozen=True, slots=True)
class WorkerClaim:
    organization_id: str
    worker_id: str
    worker_generation: str
    worker_build_id: str
    runtime_lock_digest: str
    lease_duration: timedelta
    handoff_preference_window: timedelta
    draining: bool = False

    def __post_init__(self) -> None:
        if self.lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if self.handoff_preference_window <= timedelta(0):
            raise ValueError("handoff_preference_window must be positive")


@dataclass(frozen=True, slots=True)
class ClaimedAttempt:
    attempt: RunAttempt
    thread_id: str
    run_version: int
    lease_token: str = field(repr=False)
    run: Run
    workspace_id: str


@dataclass(frozen=True, slots=True)
class SealedClaim:
    failure: SafeFailure


type ClaimResult = ClaimedAttempt | SealedClaim | None


@dataclass(frozen=True, slots=True)
class ScanPosition:
    available_at: datetime
    priority: int
    created_at: datetime
    run_id: str


@dataclass(frozen=True, slots=True)
class RunCandidate:
    """Detached scheduling metadata; preflight and claim still establish eligibility."""

    organization_id: str
    run_id: str
    runtime_lock_digest: str
    queue_name: str
    position: ScanPosition


class AttemptScheduler:
    """Use PostgreSQL-owned state to select and establish one Attempt authority."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        lifecycle: LifecycleWriter,
        clock: Clock = utc_now,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
        attempt_id_factory: Callable[[], str] = new_run_attempt_id,
    ) -> None:
        self._lifecycle = lifecycle
        self._sessions = sessions
        self._clock = clock
        self._token_factory = token_factory
        self._attempt_id_factory = attempt_id_factory

    async def scan(self, claim: WorkerClaim, *, queue_name: str, limit: int = 32) -> Sequence[str]:
        """Return a bounded deterministic superset of claimable Run identities."""

        if claim.draining:
            return ()
        candidates = await self.discover(
            worker_build_id=claim.worker_build_id,
            handoff_preference_window=claim.handoff_preference_window,
            organization_id=claim.organization_id,
            runtime_lock_digest=claim.runtime_lock_digest,
            queue_names=(queue_name,),
            limit=limit,
        )
        return tuple(candidate.run_id for candidate in candidates)

    async def discover(
        self,
        *,
        worker_build_id: str,
        handoff_preference_window: timedelta,
        limit: int = 32,
        after: ScanPosition | None = None,
        organization_id: str | None = None,
        runtime_lock_digest: str | None = None,
        queue_names: tuple[str, ...] = (),
    ) -> tuple[RunCandidate, ...]:
        """Discover work across organizations without an unbounded organization or Runtime-lock scan."""

        if limit < 1 or limit > 1024:
            raise ValueError("scan limit must be between 1 and 1024")
        if handoff_preference_window <= timedelta(0):
            raise ValueError("handoff_preference_window must be positive")
        now = assume_utc(self._clock())
        predecessor = aliased(RunAttemptRecord)
        same_build_service_drain_ready = and_(
            predecessor.yield_reason == "service_drain",
            predecessor.worker_build_id == worker_build_id,
            predecessor.finished_at <= now - handoff_preference_window,
        )
        yielded_ready = and_(
            predecessor.status == RunAttemptStatus.yielded.value,
            or_(
                predecessor.yield_reason == "runner_rotation",
                predecessor.worker_build_id != worker_build_id,
                same_build_service_drain_ready,
            ),
        )
        eligible = or_(
            and_(
                RunRecord.status == RunStatus.accepted.value,
                RunRecord.current_run_attempt_id.is_(None),
                RunRecord.available_at <= now,
            ),
            and_(
                RunRecord.status == RunStatus.running.value,
                RunRecord.current_run_attempt_id.is_(None),
                RunRecord.available_at <= now,
                or_(predecessor.status == RunAttemptStatus.failed.value, yielded_ready),
            ),
            and_(
                RunRecord.status == RunStatus.running.value,
                RunRecord.current_run_attempt_id == predecessor.id,
                predecessor.status.in_((RunAttemptStatus.leased.value, RunAttemptStatus.running.value)),
                predecessor.lease_expires_at <= now,
            ),
        )
        statement = (
            select(
                RunRecord.organization_id,
                RunRecord.id,
                RunRecord.runtime_lock_digest,
                RunRecord.queue_name,
                RunRecord.available_at,
                RunRecord.priority,
                RunRecord.created_at,
            )
            .outerjoin(EnvironmentRecord, EnvironmentRecord.id == RunRecord.environment_id)
            .outerjoin(EnvironmentProviderRecord, EnvironmentProviderRecord.id == EnvironmentRecord.provider_id)
            .outerjoin(
                predecessor,
                and_(
                    predecessor.organization_id == RunRecord.organization_id,
                    predecessor.run_id == RunRecord.id,
                    predecessor.attempt_number == RunRecord.attempts_started,
                ),
            )
            .where(
                local_backend_eligible(),
                eligible,
            )
            .order_by(
                RunRecord.available_at.asc(),
                RunRecord.priority.desc(),
                RunRecord.created_at.asc(),
                RunRecord.id.asc(),
            )
            .limit(limit)
        )
        if organization_id is not None:
            statement = statement.where(RunRecord.organization_id == organization_id)
        if runtime_lock_digest is not None:
            statement = statement.where(RunRecord.runtime_lock_digest == runtime_lock_digest)
        if queue_names:
            statement = statement.where(RunRecord.queue_name.in_(queue_names))
        if after is not None:
            same_time = RunRecord.available_at == after.available_at
            same_priority = RunRecord.priority == after.priority
            statement = statement.where(
                or_(
                    RunRecord.available_at > after.available_at,
                    and_(same_time, RunRecord.priority < after.priority),
                    and_(same_time, same_priority, RunRecord.created_at > after.created_at),
                    and_(
                        same_time,
                        same_priority,
                        RunRecord.created_at == after.created_at,
                        RunRecord.id > after.run_id,
                    ),
                )
            )
        async with short_session(self._sessions) as database:
            rows = (await database.execute(statement)).all()
            return tuple(
                RunCandidate(
                    organization_id=row.organization_id,
                    run_id=row.id,
                    runtime_lock_digest=row.runtime_lock_digest,
                    queue_name=row.queue_name,
                    position=ScanPosition(
                        available_at=assume_utc(row.available_at),
                        priority=row.priority,
                        created_at=assume_utc(row.created_at),
                        run_id=row.id,
                    ),
                )
                for row in rows
            )

    async def claim(self, run_id: str, claim: WorkerClaim) -> ClaimResult:
        """Try one exact claim or takeover; a changed candidate returns an empty result."""

        if claim.draining:
            return None
        now = assume_utc(self._clock())
        async with transaction(self._sessions) as database:
            scope = await database.scalar(
                select(RunRecord.thread_id).where(
                    RunRecord.organization_id == claim.organization_id,
                    RunRecord.id == run_id,
                )
            )
            if scope is None:
                return None
            thread = await database.scalar(
                select(ThreadRecord)
                .where(ThreadRecord.organization_id == claim.organization_id, ThreadRecord.id == scope)
                .with_for_update()
            )
            locked_runs = await lock_inbox_related_runs(
                database,
                organization_id=claim.organization_id,
                thread_id=scope,
                required_run_ids=(run_id,),
            )
            run = next((item for item in locked_runs if item.id == run_id), None)
            if run is None or thread is None or thread.current_run_id != run.id:
                return None
            if run.environment_id is not None:
                eligible_environment = await database.scalar(
                    select(EnvironmentRecord.id)
                    .join(EnvironmentProviderRecord, EnvironmentProviderRecord.id == EnvironmentRecord.provider_id)
                    .where(EnvironmentRecord.id == run.environment_id, local_backend_eligible())
                )
                if eligible_environment is None:
                    return None
            if run.runtime_lock_digest != claim.runtime_lock_digest:
                raise AttemptSchedulingError("claimant Runtime lock does not match the accepted Run")

            predecessor = await self._lock_predecessor(database, run)
            classification = _classify_candidate(run, predecessor, claim, now)
            if classification is None:
                return None
            mutation_id = new_mutation_id()
            initially_accepted = run.status == RunStatus.accepted.value

            if classification == "lease_expired":
                assert predecessor is not None
                terminalize_attempt(
                    predecessor,
                    RunAttemptStatus.failed,
                    now,
                    failure=_lease_expiry_failure(),
                )
                charge_attempt_usage(run, predecessor)

            budget_failure = _claim_budget_failure(run, classification, now)
            if budget_failure is not None:
                await schedule_environment_maintenance(database, run=run, now=now)
                await apply_run_outcome(database, run=run, outcome="failed", now=now)
                seal_failed_run(run, thread, budget_failure, now)
                if classification == "lease_expired":
                    assert predecessor is not None
                    await self._lifecycle.append_run_with_attempt_lifecycle(
                        database,
                        run,
                        "run.failed",
                        attempt=predecessor,
                        attempt_event_type="run_attempt.failed",
                        mutation_id=mutation_id,
                        occurred_at=now,
                        actor_type="worker",
                        actor_id=claim.worker_id,
                    )
                else:
                    await self._lifecycle.append_run_lifecycle(
                        database,
                        run,
                        "run.failed",
                        mutation_id=mutation_id,
                        occurred_at=now,
                        actor_type="worker",
                        actor_id=claim.worker_id,
                    )
                return SealedClaim(budget_failure)

            token = self._token_factory()
            if not token:
                raise ValueError("lease token factory returned an empty token")
            attempt = RunAttempt(
                id=self._attempt_id_factory(),
                version=1,
                organization_id=run.organization_id,
                run_id=run.id,
                attempt_number=run.attempts_started + 1,
                fence=run.next_attempt_fence,
                status=RunAttemptStatus.leased,
                replaces_run_attempt_id=(
                    predecessor.id
                    if predecessor is not None and classification in {"attempt_failed", "lease_expired"}
                    else None
                ),
                recovery_reason=None if classification == "initial" else classification,
                worker_id=claim.worker_id,
                worker_generation=claim.worker_generation,
                worker_build_id=claim.worker_build_id,
                runtime_lock_digest=claim.runtime_lock_digest,
                model_execution_observation=run.to_resource().model_execution_observation,
                lease_token_digest=_token_digest(token),
                lease_expires_at=now + claim.lease_duration,
                heartbeat_at=now,
                usage=RecoveryUsage(),
                created_at=now,
                claimed_at=now,
                updated_at=now,
            )
            attempt_record_value = run_attempt_record(attempt)
            database.add(attempt_record_value)
            run.status = RunStatus.running.value
            run.current_run_attempt_id = attempt.id
            run.attempts_started += 1
            if classification != "planned_handoff":
                run.recovery_attempts_started += 1
            run.next_attempt_fence += 1
            run.version += 1
            run.updated_at = now
            await database.flush()
            if classification == "lease_expired":
                assert predecessor is not None
                await self._lifecycle.append_run_attempt_lifecycle(
                    database,
                    run,
                    predecessor,
                    "run_attempt.failed",
                    mutation_id=mutation_id,
                    occurred_at=now,
                )
            if initially_accepted:
                await self._lifecycle.append_run_with_attempt_lifecycle(
                    database,
                    run,
                    "run.running",
                    attempt=attempt_record_value,
                    attempt_event_type="run_attempt.leased",
                    mutation_id=mutation_id,
                    occurred_at=now,
                    actor_type="worker",
                    actor_id=claim.worker_id,
                )
            else:
                await self._lifecycle.append_run_attempt_lifecycle(
                    database,
                    run,
                    attempt_record_value,
                    "run_attempt.leased",
                    mutation_id=mutation_id,
                    occurred_at=now,
                )
            workspace_id = await database.scalar(
                select(SessionRecord.workspace_id).where(
                    SessionRecord.organization_id == run.organization_id,
                    SessionRecord.id == run.session_id,
                )
            )
            if workspace_id is None:
                raise ValueError("Claimed Run has no owning Session")
            return ClaimedAttempt(
                attempt=attempt,
                thread_id=run.thread_id,
                run_version=run.version,
                lease_token=token,
                run=run.to_resource(),
                workspace_id=workspace_id,
            )

    async def _lock_predecessor(
        self,
        database: AsyncSession,
        run: RunRecord,
    ) -> RunAttemptRecord | None:
        if run.current_run_attempt_id is not None:
            return await database.scalar(
                select(RunAttemptRecord)
                .where(
                    RunAttemptRecord.organization_id == run.organization_id,
                    RunAttemptRecord.run_id == run.id,
                    RunAttemptRecord.id == run.current_run_attempt_id,
                )
                .with_for_update()
            )
        if run.attempts_started == 0:
            return None
        return await database.scalar(
            select(RunAttemptRecord)
            .where(
                RunAttemptRecord.organization_id == run.organization_id,
                RunAttemptRecord.run_id == run.id,
                RunAttemptRecord.attempt_number == run.attempts_started,
            )
            .with_for_update()
        )


def _classify_candidate(
    run: RunRecord,
    predecessor: RunAttemptRecord | None,
    claim: WorkerClaim,
    now: datetime,
) -> str | None:
    if run.status == RunStatus.accepted.value:
        if run.current_run_attempt_id is not None or run.attempts_started != 0 or assume_utc(run.available_at) > now:
            return None
        return "initial"
    if run.status != RunStatus.running.value:
        return None
    if predecessor is None:
        return None
    if run.current_run_attempt_id is not None:
        if (
            predecessor.id != run.current_run_attempt_id
            or predecessor.status not in {RunAttemptStatus.leased.value, RunAttemptStatus.running.value}
            or assume_utc(predecessor.lease_expires_at) > now
        ):
            return None
        return "lease_expired"
    if assume_utc(run.available_at) > now:
        return None
    if predecessor.status == RunAttemptStatus.failed.value:
        return "attempt_failed"
    if predecessor.status != RunAttemptStatus.yielded.value or predecessor.finished_at is None:
        return None
    if predecessor.yield_reason == "service_drain" and predecessor.worker_build_id == claim.worker_build_id:
        eligible_at = assume_utc(predecessor.finished_at) + claim.handoff_preference_window
        if now < eligible_at:
            return None
    return "planned_handoff"


def _claim_budget_failure(run: RunRecord, classification: str, now: datetime) -> SafeFailure | None:
    if run.recovery_policy_version != "1":
        return _failure("recovery_policy_unsupported", "The Run recovery policy version is unsupported.")
    if run.recovery_deadline_at is not None and now >= assume_utc(run.recovery_deadline_at):
        return _failure("recovery_deadline_exhausted", "The Run recovery deadline was exhausted.")
    resource = run.to_resource()
    if resource.recovery_budget.max_usage is not None and not resource.recovery_budget.max_usage.permits(
        resource.usage_charged
    ):
        return _failure("recovery_usage_exhausted", "The Run recovery usage budget was exhausted.")
    if classification != "planned_handoff" and run.recovery_attempts_started >= run.max_recovery_attempts:
        return _failure("recovery_attempts_exhausted", "The Run recovery attempt budget was exhausted.")
    return None


def _lease_expiry_failure() -> SafeFailure:
    return _failure("run_attempt_lease_expired", "The prior Run attempt lease expired.")


def _failure(code: str, message: str) -> SafeFailure:
    return SafeFailure(code=code, message=message, retry_hint="none")


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


__all__ = [
    "AttemptScheduler",
    "AttemptSchedulingError",
    "ClaimResult",
    "ClaimedAttempt",
    "RunCandidate",
    "ScanPosition",
    "SealedClaim",
    "WorkerClaim",
]
