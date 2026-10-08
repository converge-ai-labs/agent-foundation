"""Claim: turning due accepted runs into leased attempts. PostgreSQL decides ownership; wakeups only hurry it.

Claim locks run → attempt and never the thread; archive and interrupt take thread → run, so the run lock
arbitrates between them. Every attempt but a handoff's successor is charged to the run's `max_attempts`: a
planned handoff is not a failure, and each one needs its worker to drain, which then claims nothing more.
"""

import secrets
from datetime import timedelta

from a13n_logging import get_logger
from sqlalchemy import Integer, String, column, func, insert, select, true, update, values

from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import now, transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.outbox import enqueue_batch
from a13n_service.infra.telemetry import meter
from a13n_service.resources.subscriptions.delivery import Transition, prepare_webhooks
from a13n_service.runs import checkpoints
from a13n_service.runs.attempts import Lease, lock_lease
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import AttemptRow, RunRow
from a13n_service.runs.webhooks import lifecycle_transition, notify_subscribers

logger = get_logger(__name__)

QUEUE_WAIT = meter.create_histogram(
    "a13n.attempt.queue_wait",
    unit="s",
    description="Time a due run waited before a worker claimed it",
    explicit_bucket_boundaries_advisory=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600),
)


async def claim(runtime: Runtime, *, worker_id: str, worker_build: str, limit: int) -> list[Lease]:
    """Lease up to `limit` due accepted runs, skipping rows other workers hold."""
    leases: list[Lease] = []
    waits: list[float] = []
    async with transaction(runtime.storage) as session:
        current = await now(session)
        runs = (
            await session.scalars(
                select(RunRow)
                .where(RunRow.status == "accepted", RunRow.available_at <= current, checkpoints.claimable())
                .order_by(RunRow.available_at, RunRow.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        if not runs:
            return []
        latest = (
            select(AttemptRow.id, AttemptRow.number, AttemptRow.status)
            .where(AttemptRow.run_id == RunRow.id)
            .order_by(AttemptRow.number.desc())
            .limit(1)
            .lateral()
        )
        previous_by_run = {
            row.run_id: row
            for row in await session.execute(
                select(RunRow.id.label("run_id"), latest.c.id, latest.c.number, latest.c.status)
                .select_from(RunRow)
                .join(latest, true())
                .where(RunRow.id.in_([run.id for run in runs]))
            )
        }
        attempts: list[dict] = []
        updates: list[tuple[str, str, int]] = []
        transitions: list[Transition] = []
        takeovers: list[tuple[RunRow, int | None]] = []
        for run in runs:
            # Core writes below own persistence; these detached rows are transition snapshots only.
            session.expunge(run)
            previous = previous_by_run.get(run.id)
            handoff = previous is not None and previous.status == "yielded"
            token = secrets.token_urlsafe(32)
            attempt = AttemptRow(
                id=new_object_id("rat"),
                organization_id=run.organization_id,
                workspace_id=run.workspace_id,
                run_id=run.id,
                number=previous.number + 1 if previous is not None else 1,
                status="leased",
                start_reason="initial" if previous is None else ("handoff" if handoff else "recovery"),
                replaces_attempt_id=previous.id if previous is not None else None,
                worker_id=worker_id,
                worker_build=worker_build,
                lease_token_hash=secret_hash(token),
                lease_expires_at=current + timedelta(seconds=runtime.settings.worker.lease_seconds),
                heartbeat_at=current,
            )
            attempts.append(
                {
                    name: getattr(attempt, name)
                    for name in (
                        "id",
                        "organization_id",
                        "workspace_id",
                        "run_id",
                        "number",
                        "status",
                        "start_reason",
                        "replaces_attempt_id",
                        "worker_id",
                        "worker_build",
                        "lease_token_hash",
                        "lease_expires_at",
                        "heartbeat_at",
                    )
                }
            )
            updates.append((run.id, attempt.id, int(not handoff)))
            if not handoff:
                run.attempts += 1
            waits.append((current - run.available_at).total_seconds())
            run.status, run.current_attempt_id = "running", attempt.id
            run.started_at = run.started_at or current
            transitions.append(
                lifecycle_transition(run, ["run.running", "run_attempt.leased"], at=current, attempt=attempt)
            )
            if previous is not None:
                takeovers.append((run, attempt.number))
            leases.append(
                Lease(
                    run_id=run.id,
                    attempt_id=attempt.id,
                    thread_id=run.thread_id,
                    organization_id=run.organization_id,
                    workspace_id=run.workspace_id,
                    number=attempt.number,
                    worker_id=worker_id,
                    token=token,
                )
            )
        await session.execute(insert(AttemptRow).values(attempts))
        claimed = values(
            column("run_id", String), column("attempt_id", String), column("charged", Integer), name="claimed"
        ).data(updates)
        changed = set(
            await session.scalars(
                update(RunRow)
                .where(RunRow.id == claimed.c.run_id, RunRow.status == "accepted")
                .values(
                    status="running",
                    current_attempt_id=claimed.c.attempt_id,
                    attempts=RunRow.attempts + claimed.c.charged,
                    started_at=func.coalesce(RunRow.started_at, current),
                )
                .returning(RunRow.id)
                .execution_options(synchronize_session=False)
            )
        )
        if changed != {run.id for run in runs}:
            raise RuntimeError("Claimed run set changed while locked")
        deliveries = await prepare_webhooks(
            session, runtime.keys, transitions, limit=runtime.settings.control.subscriptions
        )
        deliveries.extend(await checkpoints.prepare_reclaims(session, takeovers))
        await enqueue_batch(session, deliveries)
    for lease, wait in zip(leases, waits, strict=True):
        QUEUE_WAIT.record(wait)
        logger.info(
            "Attempt claimed",
            extra={"run_id": lease.run_id, "attempt_id": lease.attempt_id, "queue_wait_ms": round(wait * 1000)},
        )
    return leases


async def start(runtime: Runtime, lease: Lease, *, harness_run_id: str) -> None:
    """The attempt is executing: record the Harness run that owns its traces and usage."""
    async with transaction(runtime.storage) as session:
        run, attempt, current = await lock_lease(session, lease)
        attempt.status, attempt.started_at, attempt.harness_run_id = "running", current, harness_run_id
        await session.flush()
        await notify_subscribers(session, runtime, run, ["run_attempt.running"], at=current, attempt=attempt)
