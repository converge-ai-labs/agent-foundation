"""Schedule and occurrence notices staged with their durable state transitions."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.resources.subscriptions.delivery import Subject, stage_webhooks
from a13n_service.resources.subscriptions.schemas import LifecycleKind
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schedules.schemas import OccurrenceSummary
from a13n_service.runs.schedules.tables import OccurrenceRow, ScheduleRow

_SCHEDULE_KINDS: dict[str, LifecycleKind] = {
    "enabled": "schedule.enabled",
    "paused": "schedule.paused",
    "completed": "schedule.completed",
    "archived": "schedule.archived",
}
_OCCURRENCE_KINDS: dict[str, LifecycleKind] = {
    "submitted": "schedule_occurrence.submitted",
    "skipped": "schedule_occurrence.skipped",
    "failed": "schedule_occurrence.failed",
    "cancelled": "schedule_occurrence.cancelled",
    "completed": "schedule_occurrence.completed",
}


async def schedule_changed(session: AsyncSession, runtime: Runtime, row: ScheduleRow, at: datetime) -> None:
    await session.flush()
    await stage_webhooks(
        session,
        runtime.keys,
        Subject(row.workspace_id, row.invocation["agent_id"], row.session_id, None, row.id),
        [_SCHEDULE_KINDS[row.state]],
        {
            "occurred_at": at.isoformat(),
            "workspace_id": row.workspace_id,
            "schedule": {
                "id": row.id,
                "session_id": row.session_id,
                "agent_id": row.invocation["agent_id"],
                "name": row.name,
                "state": row.state,
                "pause_reason": row.pause_reason,
                "version": row.version,
            },
        },
        limit=runtime.settings.control.subscriptions,
    )


async def occurrence_changed(session: AsyncSession, runtime: Runtime, item: OccurrenceRow, at: datetime) -> None:
    if item.status == "pending":
        return
    await session.flush()
    await stage_webhooks(
        session,
        runtime.keys,
        Subject(
            item.workspace_id,
            item.snapshot["invocation"]["agent_id"],
            item.session_id,
            item.thread_id,
            item.schedule_id,
        ),
        [_OCCURRENCE_KINDS[item.status]],
        {
            "occurred_at": at.isoformat(),
            "workspace_id": item.workspace_id,
            "occurrence": OccurrenceSummary.model_validate(item).model_dump(mode="json"),
        },
        limit=runtime.settings.control.subscriptions,
    )
