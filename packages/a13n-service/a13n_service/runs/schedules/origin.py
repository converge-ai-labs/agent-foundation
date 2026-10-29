"""Resolve execution provenance from Run input, with one read per batch."""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.runs.schedules.tables import OccurrenceRow
from a13n_service.runs.schemas import ScheduleOrigin
from a13n_service.runs.tables import InboxEntryRow, RunRow


async def origins(session: AsyncSession, run_ids: Iterable[str]) -> dict[str, ScheduleOrigin]:
    ids = set(run_ids)
    if not ids:
        return {}
    rows = await session.execute(
        select(RunRow.id, OccurrenceRow.schedule_id, OccurrenceRow.id)
        .join(InboxEntryRow, InboxEntryRow.id == RunRow.occurrence_trigger_entry_id)
        .join(OccurrenceRow, OccurrenceRow.id == InboxEntryRow.schedule_occurrence_id)
        .where(RunRow.id.in_(ids))
    )
    return {
        run_id: ScheduleOrigin(schedule_id=schedule_id, occurrence_id=occurrence_id)
        for run_id, schedule_id, occurrence_id in rows
    }
