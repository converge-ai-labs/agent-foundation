"""The one worker predicate: every execution-dependent write proves it holds the current, unexpired lease."""

import asyncio
import hmac
import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import exists, func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import Storage, lock, now, transaction
from a13n_service.infra.errors import ServiceError, not_found
from a13n_service.runs.schemas import Outcome
from a13n_service.runs.tables import AttemptRow, RunRow, ThreadRow
from a13n_service.tenancy.access import Access, principals_for, refuse_archived
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope, authorize
from a13n_service.tenancy.tables import WorkspaceRow

# Attempt statuses under which a lease can be held.
_HOLDING = ("leased", "running")


@dataclass(frozen=True, slots=True)
class Lease:
    """A worker's claim on one attempt. The token exists only in the worker's memory; the row stores its hash."""

    run_id: str
    attempt_id: str
    thread_id: str
    organization_id: str
    workspace_id: str
    number: int
    worker_id: str
    token: str = field(repr=False)


@dataclass
class AttemptControl:
    """Signals from the attempt's supervisor to its execution, checked at safe boundaries and before dispatch."""

    # Set when the run must stop dispatching and seal `outcome`: it was interrupted, or its principal lost the
    # authority to run it.
    stopped: asyncio.Event = field(default_factory=asyncio.Event)
    outcome: Outcome = field(default_factory=Outcome.cancelled)
    # The worker is draining: yield the run at the next safe boundary.
    handoff: asyncio.Event = field(default_factory=asyncio.Event)
    # Event-loop time when the lease runs out unless renewed, measured before the claim or renewal was sent.
    deadline: float = math.inf
    # Renewal is due every third of a lease; a lease with less left has missed a confirmed renewal.
    renewal_margin: float = 0.0

    def stop(self, outcome: Outcome) -> None:
        if not self.stopped.is_set():
            self.outcome = outcome
            self.stopped.set()

    def expiring(self, margin: float) -> bool:
        """Whether the lease runs out within `margin` seconds unless a renewal is confirmed first."""
        return asyncio.get_running_loop().time() + margin >= self.deadline

    def renewal_missed(self) -> bool:
        """The lease can run out before another renewal is confirmed, so no new work may start under it."""
        return self.expiring(self.renewal_margin)


class LeaseLost(Exception):
    """The attempt no longer holds its lease, so nothing it does may be written. Deliberately not a
    `ServiceError`: no handler of refusals may turn it into an outcome, and it never reaches an API caller."""

    def __init__(self) -> None:
        super().__init__("Worker lease is no longer current")


class AuthorityRevoked(Exception):
    """The run's principal may no longer run it, so the run fails with `outcome`. Like `LeaseLost`, deliberately
    not a `ServiceError`, so no handler of refusals turns it into another outcome."""

    def __init__(self) -> None:
        super().__init__("The run's principal can no longer run it")
        self.outcome = Outcome.failed("authority_revoked", str(self))


async def execution_principals(
    session: AsyncSession, access: Access, runs: Collection[RunRow]
) -> dict[str, Principal | ServiceError]:
    """Each run's principal while its workspace is not archived and the principal's current status and grants
    still allow the frozen authority to run the run, or else the refusal, by run ID. Set reads serve any number of
    runs. `unavailable` is raised: it refuses nothing."""
    if not runs:
        return {}
    workspaces = {
        workspace.id: workspace
        for workspace in await session.scalars(
            select(WorkspaceRow).where(WorkspaceRow.id.in_({run.workspace_id for run in runs}))
        )
    }
    scopes = {run.id: WorkspaceScope(run.organization_id, run.workspace_id) for run in runs}
    principals = await principals_for(session, access, [(run.principal_id, scopes[run.id]) for run in runs])
    results: dict[str, Principal | ServiceError] = {}
    for run in runs:
        scope = scopes[run.id]
        principal = principals[run.principal_id, scope]
        try:
            workspace = workspaces.get(run.workspace_id)
            if workspace is None:
                raise not_found("workspace", run.workspace_id)
            refuse_archived(workspace)
            if isinstance(principal, Principal):
                authorize(principal, scope, "run", authority=ExecutionAuthority.model_validate(run.authority))
        except ServiceError as refusal:
            principal = refusal
        results[run.id] = principal
    return results


async def authorize_execution(session: AsyncSession, access: Access, run: RunRow) -> Principal:
    """The run's principal, while it may still run the run. Any refusal but `unavailable` is `AuthorityRevoked`."""
    principal = (await execution_principals(session, access, [run]))[run.id]
    if isinstance(principal, ServiceError):
        raise AuthorityRevoked() from principal
    return principal


def holds(lease: Lease, run: RunRow, attempt: AttemptRow, current: datetime) -> bool:
    """`current` must be read after the run and attempt locks were acquired: transaction time can be stale."""
    return (
        run.id == lease.run_id
        and run.status == "running"
        and run.current_attempt_id == attempt.id == lease.attempt_id
        and attempt.run_id == run.id
        and attempt.status in _HOLDING
        and attempt.worker_id == lease.worker_id
        and hmac.compare_digest(attempt.lease_token_hash, secret_hash(lease.token))
        and attempt.lease_expires_at > current
    )


async def lock_lease(session: AsyncSession, lease: Lease) -> tuple[RunRow, AttemptRow, datetime]:
    """Lock run → attempt and prove the lease; claim uses this suffix of the lock order."""
    run = await lock(session, RunRow, lease.run_id)
    attempt = await lock(session, AttemptRow, lease.attempt_id)
    current = await now(session)
    if run is None or attempt is None or not holds(lease, run, attempt, current):
        raise LeaseLost()
    return run, attempt, current


async def lock_thread_lease(session: AsyncSession, lease: Lease) -> tuple[ThreadRow, RunRow, AttemptRow, datetime]:
    """Thread → run → attempt, for writes that also change the thread or its inbox."""
    thread = await lock(session, ThreadRow, lease.thread_id)
    if thread is None:
        raise LeaseLost()
    run, attempt, current = await lock_lease(session, lease)
    return thread, run, attempt, current


async def prove(storage: Storage, lease: Lease) -> None:
    """Raise `LeaseLost` unless the lease is still current, for work that must not continue without it."""
    async with transaction(storage) as session:
        await lock_lease(session, lease)


async def renew(
    storage: Storage, access: Access, leases: Sequence[Lease], *, extend: Collection[str], seconds: float
) -> tuple[dict[str, Outcome], set[str]]:
    """One heartbeat of a worker's attempts. Returns, by attempt ID, the outcome each run must stop with (cancelled
    once an interrupt was requested, failed once its principal lost the authority to run it) and the leases named
    in `extend` that it extended by `seconds` from database time. Both hold only once this has returned.

    Cancellation and authority are read without locks. An `unavailable` authority check fails the heartbeat,
    which then extends nothing.
    """
    async with transaction(storage) as session:
        runs = {
            run.id: run
            for run in await session.scalars(select(RunRow).where(RunRow.id.in_({lease.run_id for lease in leases})))
        }
        principals = await execution_principals(
            session, access, [run for run in runs.values() if run.cancel_requested_at is None]
        )
        stops: dict[str, Outcome] = {}
        for lease in leases:
            run = runs.get(lease.run_id)
            if run is None:
                continue
            if run.cancel_requested_at is not None:
                stops[lease.attempt_id] = Outcome.cancelled()
            elif isinstance(principals[run.id], ServiceError):
                stops[lease.attempt_id] = AuthorityRevoked().outcome
        due = [lease for lease in leases if lease.attempt_id in extend]
        extended = await _extend(session, due, seconds) if due else set()
    return stops, extended


async def _extend(session: AsyncSession, leases: Sequence[Lease], seconds: float) -> set[str]:
    """Prove each lease on its locked attempt row and extend it, never reviving an expired one. Rows another
    transaction holds are skipped rather than waited for, so a heartbeat never waits on, or deadlocks with, an
    execution's own writes; those leases stay due for the next heartbeat."""
    locked = (
        await session.scalars(
            select(AttemptRow.id)
            .where(
                tuple_(AttemptRow.id, AttemptRow.worker_id, AttemptRow.lease_token_hash).in_(
                    [(lease.attempt_id, lease.worker_id, secret_hash(lease.token)) for lease in leases]
                )
            )
            .order_by(AttemptRow.id)
            .with_for_update(skip_locked=True)
        )
    ).all()
    if not locked:
        return set()
    current = exists().where(
        RunRow.id == AttemptRow.run_id, RunRow.status == "running", RunRow.current_attempt_id == AttemptRow.id
    )
    extended = await session.scalars(
        update(AttemptRow)
        .where(
            AttemptRow.id.in_(locked),
            AttemptRow.status.in_(_HOLDING),
            AttemptRow.lease_expires_at > func.clock_timestamp(),
            current,
        )
        .values(
            heartbeat_at=func.clock_timestamp(),
            lease_expires_at=func.clock_timestamp() + func.make_interval(0, 0, 0, 0, 0, 0, seconds),
        )
        .returning(AttemptRow.id)
        .execution_options(synchronize_session=False)
    )
    return set(extended)
