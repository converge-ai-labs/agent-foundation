"""Complete relational Run failure after the current Attempt has been accounted for."""

from datetime import datetime

from a13n_harness import SafeFailure
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.environments.usage import refresh_run_retention

from ._transitions import seal_failed_run
from .inbox_persistence import apply_run_outcome
from .lifecycle import LifecycleWriter
from .models import RunAttemptRecord, RunRecord, ThreadRecord


async def finalize_failed_run(
    database: AsyncSession,
    *,
    run: RunRecord,
    thread: ThreadRecord,
    failure: SafeFailure,
    now: datetime,
    lifecycle: LifecycleWriter,
    actor_id: str,
    mutation_id: str,
    failed_attempt: RunAttemptRecord | None = None,
) -> None:
    """Seal, release inbox/retention, and publish one complete failure transaction."""
    seal_failed_run(run, thread, failure, now)
    await refresh_run_retention(database, run=run, now=now)
    await apply_run_outcome(database, thread=thread, run=run, outcome="failed", now=now)
    if failed_attempt is None:
        await lifecycle.append_run_lifecycle(
            database,
            run,
            "run.failed",
            mutation_id=mutation_id,
            occurred_at=now,
            actor_type="worker",
            actor_id=actor_id,
        )
    else:
        await lifecycle.append_run_with_attempt_lifecycle(
            database,
            run,
            "run.failed",
            attempt=failed_attempt,
            attempt_event_type="run_attempt.failed",
            mutation_id=mutation_id,
            occurred_at=now,
            actor_type="worker",
            actor_id=actor_id,
        )
