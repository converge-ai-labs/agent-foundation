"""Resolve execution provenance from Thread ownership, with one read per batch."""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.runs.schedules.tables import OccurrenceRow
from a13n_service.runs.schemas import ScheduleOrigin
from a13n_service.runs.tables import ThreadRow


async def origins(session: AsyncSession, thread_ids: Iterable[str]) -> dict[str, ScheduleOrigin]:
    rows = await session.execute(
        select(ThreadRow.id, OccurrenceRow.schedule_id, OccurrenceRow.id)
        .join(OccurrenceRow, OccurrenceRow.id == ThreadRow.schedule_occurrence_id)
        .where(ThreadRow.id.in_(set(thread_ids)))
    )
    return {
        thread_id: ScheduleOrigin(schedule_id=schedule_id, occurrence_id=occurrence_id)
        for thread_id, schedule_id, occurrence_id in rows
    }
