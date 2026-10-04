"""Ending an attempt: sealing its run, or returning the run to accepted for recovery or handoff.

Three callers hold different authority (worker lease, interrupt of an accepted run, the expiry sweep) but
share one transition, so thread pointers, input disposition, child notification and webhooks are decided
in one place. Cleanup is staged durably with the transition. The transaction owner advances successors
after commit; only telemetry uses callbacks. Every sealed run is the history its thread continues, but failed or
cancelled runs pause their thread's automatic advancement.
"""

from datetime import datetime, timedelta
from typing import Literal

from a13n_logging import exception_details, get_logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import after_commit, lock, now, transaction
from a13n_service.infra.outbox import enqueue_once
from a13n_service.infra.telemetry import meter
from a13n_service.runs import checkpoints, inbox
from a13n_service.runs.accept import advance
from a13n_service.runs.attempts import Lease, lock_thread_lease
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import Failure, Outcome, Sealed
from a13n_service.runs.tables import AttemptRow, RunRow, ThreadRow
from a13n_service.runs.usage import totals
from a13n_service.runs.webhooks import LifecycleKind, notify_subscribers

logger = get_logger(__name__)

ATTEMPT_DURATION = meter.create_histogram(
    "a13n.attempt.duration",
    unit="s",
    description="Time from claim to the end of an attempt, by how it ended",
    explicit_bucket_boundaries_advisory=(1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600),
)
RUNS_SEALED = meter.create_counter(
    "a13n.runs.sealed", unit="{run}", description="Sealed runs by status, and by failure code when they failed"
)

_ATTEMPT_STATUS: dict[Sealed, str] = {
    "completed": "succeeded",
    "waiting": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
}
_RUN_EVENTS: dict[str, LifecycleKind] = {
    "accepted": "run.accepted",
    "waiting": "run.waiting",
    "completed": "run.completed",
    "failed": "run.failed",
    "cancelled": "run.cancelled",
}
_ATTEMPT_EVENTS: dict[str, LifecycleKind] = {
    "succeeded": "run_attempt.succeeded",
    "yielded": "run_attempt.yielded",
    "failed": "run_attempt.failed",
    "cancelled": "run_attempt.cancelled",
}


def _kinds(run_status: str, attempt_status: str | None) -> list[LifecycleKind]:
    return [_RUN_EVENTS[run_status], *([_ATTEMPT_EVENTS[attempt_status]] if attempt_status else [])]


def recovery_delay(attempts: int) -> timedelta:
    """Backoff before a recovered run is claimable again; bounded so recovery never stalls for long."""
    return timedelta(seconds=min(60, 2 ** max(0, attempts - 1)))


def _finish_attempt(
    session: AsyncSession, attempt: AttemptRow, status: str, *, at: datetime, failure: Failure | None = None
) -> None:
    attempt.status, attempt.finished_at = status, at
    if failure is not None:
        attempt.failure = failure.model_dump()
    seconds = (at - attempt.created_at).total_seconds()
    fields = {
        "run_id": attempt.run_id,
        "attempt_id": attempt.id,
        "status": status,
        "reason": failure.code if failure is not None else attempt.yield_reason,
        "duration_ms": round(seconds * 1000),
    }

    async def ended() -> None:
        ATTEMPT_DURATION.record(seconds, {"status": status})
        logger.info("Attempt ended", extra=fields)

    after_commit(session, ended)


def _sealed(session: AsyncSession, run: RunRow, outcome: Outcome) -> None:
    reason = outcome.failure.code if outcome.failure is not None else None
    labels = {"status": outcome.status} | ({"reason": reason} if reason is not None else {})
    fields = {"run_id": run.id, "status": outcome.status, "reason": reason}

    async def sealed() -> None:
        RUNS_SEALED.add(1, labels)
        logger.info("Run sealed", extra=fields)

    after_commit(session, sealed)


async def seal(
    session: AsyncSession,
    runtime: Runtime,
    thread: ThreadRow,
    run: RunRow,
    attempt: AttemptRow | None,
    outcome: Outcome,
    *,
    at: datetime,
) -> None:
    """The terminal transition. The caller holds thread → run → attempt locks and has verified its authority."""
    # Read before the run changes: the query would flush a sealed status, and sealed facts are immutable.
    usage = await totals(session, run.id)
    if attempt is not None:
        _finish_attempt(session, attempt, _ATTEMPT_STATUS[outcome.status], at=at, failure=outcome.failure)
    _sealed(session, run, outcome)
    kinds = _kinds(outcome.status, attempt.status if attempt is not None else None)
    run.status, run.sealed_at, run.current_attempt_id = outcome.status, at, None
    run.output = outcome.output
    if outcome.pending is not None:
        run.pending, run.wait_reason = outcome.pending.model_dump(mode="json"), outcome.pending.reason
    if outcome.failure is not None:
        run.failure = outcome.failure.model_dump()
    run.usage_at_seal = usage
    thread.current_run_id, thread.last_run_id = None, run.id
    await session.flush()
    await inbox.release_assigned(session, thread, run, at=at)
    if thread.origin == "child" and outcome.status != "waiting":
        # One result per sealed child run; the parent's delivery validates and inserts it later.
        await enqueue_once(
            session,
            organization_id=run.organization_id,
            workspace_id=run.workspace_id,
            kind="child_result",
            dedupe_key=run.id,
            target={"thread_id": thread.origin_thread_id, "origin_run_id": thread.origin_run_id},
            payload={"child_run_id": run.id},
        )
    await notify_subscribers(session, runtime, run, kinds, at=at, attempt=attempt)
    await checkpoints.reclaim(session, run)


async def recover(
    session: AsyncSession,
    runtime: Runtime,
    thread: ThreadRow,
    run: RunRow,
    attempt: AttemptRow,
    *,
    at: datetime,
    status: Literal["yielded", "failed"],
    failure: Failure | None = None,
    yield_reason: str | None = None,
) -> None:
    """Close the attempt and return the run to accepted, or fail it when a failed attempt was its last one.

    Assignments stay with the run: the next attempt restores the last checkpoint and reoffers what is
    still assigned. A handoff is immediately due and uses none of `max_attempts`; a failure waits out a
    bounded backoff.
    """
    if run.cancel_requested_at is not None:
        await seal(session, runtime, thread, run, attempt, Outcome.cancelled(), at=at)
        return
    if status == "failed" and run.attempts >= run.max_attempts:
        await seal(session, runtime, thread, run, attempt, Outcome(status="failed", failure=failure), at=at)
        return
    attempt.yield_reason = yield_reason
    _finish_attempt(session, attempt, status, at=at, failure=failure)
    run.status, run.current_attempt_id = "accepted", None
    run.available_at = at if status == "yielded" else at + recovery_delay(run.attempts)
    await session.flush()
    await notify_subscribers(session, runtime, run, _kinds("accepted", status), at=at, attempt=attempt)
    if run.available_at <= at:
        runtime.wake_workers(session)


async def stop(session: AsyncSession, runtime: Runtime, thread: ThreadRow, run: RunRow) -> None:
    """Cancel an active run under its thread and run locks: an accepted run seals now, a running one is asked to
    stop at its next safe boundary. A sealed run is left as it is."""
    current = await now(session)
    if run.status == "accepted":
        await seal(session, runtime, thread, run, None, Outcome.cancelled(), at=current)
    elif run.status == "running" and run.cancel_requested_at is None:
        run.cancel_requested_at = current
    await session.flush()


async def seal_attempt(
    runtime: Runtime, lease: Lease, outcome: Outcome, *, display: checkpoints.DisplayWrite | None = None
) -> None:
    """The worker's seal of a failure or cancellation; a completed or waiting outcome seals in its final checkpoint's
    transaction instead.

    A failed or cancelled attempt may pass the display it folded, its unfinished items interrupted, which becomes
    the run's final display in the same transaction; the state pointer stays at the last checkpoint, which a
    successor continues.
    """
    async with transaction(runtime.storage) as session:
        thread, run, attempt, current = await lock_thread_lease(session, lease)
        if display is not None:
            checkpoints.record_display(session, run, display)
        await seal(session, runtime, thread, run, attempt, outcome, at=current)
    if outcome.status in {"completed", "waiting"}:
        await advance(runtime, lease.thread_id)


async def release_attempt(
    runtime: Runtime,
    lease: Lease,
    *,
    status: Literal["yielded", "failed"],
    failure: Failure | None = None,
    yield_reason: str | None = None,
) -> None:
    """The worker gives the run back: a planned handoff, or a transient failure within `max_attempts`."""
    async with transaction(runtime.storage) as session:
        thread, run, attempt, current = await lock_thread_lease(session, lease)
        await recover(
            session,
            runtime,
            thread,
            run,
            attempt,
            at=current,
            status=status,
            failure=failure,
            yield_reason=yield_reason,
        )


async def expire_leases(runtime: Runtime, *, batch: int) -> None:
    """The expire_leases sweep: close current attempts whose lease ran out, rechecked under locks."""
    async with transaction(runtime.storage) as session:
        expired = (
            await session.execute(
                select(RunRow.thread_id, RunRow.id, AttemptRow.id)
                .join(RunRow, RunRow.current_attempt_id == AttemptRow.id)
                .where(AttemptRow.status.in_(("leased", "running")), AttemptRow.lease_expires_at < await now(session))
                .order_by(AttemptRow.lease_expires_at)
                .limit(batch)
            )
        ).all()
    for thread_id, run_id, attempt_id in expired:
        try:
            await _expire(runtime, thread_id, run_id, attempt_id)
        except Exception as error:
            # One run that cannot be recovered now must not hold up the others; the next sweep retries it.
            logger.warning(
                "Lease expiry failed",
                extra={
                    "run_id": run_id,
                    "error_type": type(error).__name__,
                    "exception_details": exception_details(error),
                },
            )


async def _expire(runtime: Runtime, thread_id: str, run_id: str, attempt_id: str) -> None:
    async with transaction(runtime.storage) as session:
        thread = await lock(session, ThreadRow, thread_id)
        run = await lock(session, RunRow, run_id)
        attempt = await lock(session, AttemptRow, attempt_id)
        current = await now(session)
        if (
            thread is None
            or run is None
            or attempt is None
            or run.current_attempt_id != attempt.id
            or attempt.status not in {"leased", "running"}
            or attempt.lease_expires_at >= current
        ):
            return
        await recover(
            session,
            runtime,
            thread,
            run,
            attempt,
            at=current,
            status="failed",
            failure=Failure(code="lease_expired", message="The worker stopped renewing its lease"),
        )
