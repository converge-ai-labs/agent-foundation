"""Complete an occurrence at its root Thread's first final Run; repair older history in bounded batches."""

from datetime import datetime
from functools import partial

from a13n_logging import get_logger
from sqlalchemy import select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import after_commit, transaction
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schedules.tables import OccurrenceRow
from a13n_service.runs.schedules.webhooks import occurrence_changed
from a13n_service.runs.tables import RunRow, ThreadRow

logger = get_logger(__name__)
_OUTCOMES = {"completed": "succeeded", "failed": "failed", "cancelled": "cancelled"}


async def _log(occurrence_id: str, run_id: str, outcome: str) -> None:
    logger.info(
        "Schedule occurrence completed",
        extra={
            "occurrence_id": occurrence_id,
            "run_id": run_id,
            "execution_outcome": outcome,
        },
    )


async def _complete(
    session: AsyncSession,
    *,
    occurrence_id: str,
    workspace_id: str,
    thread_id: str,
    run_id: str,
    status: str,
    at: datetime,
) -> OccurrenceRow | None:
    # No Schedule lock: submission locks Schedule → Occurrence, while sealing already holds Thread → Run.
    # The root identity and submitted predicate arbitrate completion and prevent child/continuation notifications.
    item = await session.scalar(
        update(OccurrenceRow)
        .where(
            OccurrenceRow.id == occurrence_id,
            OccurrenceRow.workspace_id == workspace_id,
            OccurrenceRow.thread_id == thread_id,
            OccurrenceRow.status == "submitted",
        )
        .values(status="completed", completion_run_id=run_id, completed_at=at, execution_outcome=_OUTCOMES[status])
        .returning(OccurrenceRow)
        .execution_options(populate_existing=True)
    )
    if item is not None:
        after_commit(session, partial(_log, item.id, run_id, _OUTCOMES[status]))
    return item


async def complete(session: AsyncSession, runtime: Runtime, thread: ThreadRow, run: RunRow, at: datetime) -> None:
    if thread.schedule_occurrence_id is None or thread.origin == "child" or run.status not in _OUTCOMES:
        return
    # Historical submitted rows may already have an earlier final Run before backfill reaches them.
    first = (
        await session.execute(
            select(RunRow.id, RunRow.status, RunRow.sealed_at)
            .join(OccurrenceRow, OccurrenceRow.thread_id == RunRow.thread_id)
            .where(
                OccurrenceRow.id == thread.schedule_occurrence_id,
                OccurrenceRow.status == "submitted",
                RunRow.thread_id == thread.id,
                RunRow.workspace_id == thread.workspace_id,
                RunRow.status.in_(tuple(_OUTCOMES)),
            )
            .order_by(RunRow.sealed_at, RunRow.id)
            .limit(1)
        )
    ).one_or_none()
    if first is None:
        return
    item = await _complete(
        session,
        occurrence_id=thread.schedule_occurrence_id,
        workspace_id=thread.workspace_id,
        thread_id=thread.id,
        run_id=first.id,
        status=first.status,
        at=first.sealed_at,
    )
    if item is not None and first.id == run.id:
        await occurrence_changed(session, runtime, item, at)


async def backfill(runtime: Runtime) -> None:
    """Repair pre-upgrade completed execution without replaying historical notifications.

    New seals persist completion atomically. Older workers during a rolling upgrade are covered on the next sweep.
    The legacy unfinished predicate remains correct until every old occurrence has been repaired.
    """
    async with transaction(runtime.storage) as session:
        first = (
            select(RunRow.id, RunRow.status, RunRow.sealed_at)
            .where(
                RunRow.thread_id == OccurrenceRow.thread_id,
                RunRow.workspace_id == OccurrenceRow.workspace_id,
                RunRow.status.in_(tuple(_OUTCOMES)),
            )
            .order_by(RunRow.sealed_at, RunRow.id)
            .limit(1)
            .lateral()
        )
        rows = (
            await session.execute(
                select(
                    OccurrenceRow.id,
                    OccurrenceRow.workspace_id,
                    OccurrenceRow.thread_id,
                    first.c.id.label("run_id"),
                    first.c.status,
                    first.c.sealed_at,
                )
                .join(first, true())
                .where(OccurrenceRow.status == "submitted")
                .order_by(OccurrenceRow.id)
                .limit(runtime.settings.schedules.batch)
                .with_for_update(of=OccurrenceRow, skip_locked=True)
            )
        ).all()
        for row in rows:
            await _complete(
                session,
                occurrence_id=row.id,
                workspace_id=row.workspace_id,
                thread_id=row.thread_id,
                run_id=row.run_id,
                status=row.status,
                at=row.sealed_at,
            )
