"""Complete an occurrence at its root Thread's first final Run."""

from datetime import datetime
from functools import partial

from a13n_logging import get_logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import after_commit
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
    # Only the first final Run can complete an occurrence; historical results remain unchanged.
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
    if first is None or first.id != run.id:
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
    if item is not None:
        await occurrence_changed(session, runtime, item, at)
