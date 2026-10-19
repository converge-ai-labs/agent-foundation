"""Bounded Schedule snapshots: recent trigger summaries and every currently unfinished Thread."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.runs.schedules.queries import unfinished_occurrence, unfinished_thread
from a13n_service.runs.schedules.schemas import CronSchedule, OccurrenceSummary, ScheduleState
from a13n_service.runs.schedules.tables import OccurrenceRow, ScheduleRow
from a13n_service.runs.tables import ThreadRow


class Configuration(BaseModel):
    id: str
    session_id: str
    name: str
    state: ScheduleState
    pause_reason: str | None
    version: int
    principal_id: str
    schedule: CronSchedule
    max_lateness_seconds: int


class Progress(BaseModel):
    next_fire_at: datetime | None
    pending_occurrence_id: str | None
    last_occurrence_id: str | None


class ScheduleEvent(BaseModel):
    session_id: str
    schedule_id: str
    configuration: Configuration
    progress: Progress


class OccurrenceEvent(BaseModel):
    session_id: str
    schedule_id: str
    occurrence: OccurrenceSummary


@dataclass(frozen=True, slots=True)
class Snapshot:
    schedule: ScheduleEvent
    occurrences: list[OccurrenceEvent]
    thread_ids: list[str]
    departing_occurrences: list[OccurrenceEvent]


async def snapshot(
    session: AsyncSession,
    workspace_id: str,
    session_id: str,
    history_limit: int,
    previous_occurrences: Sequence[str] = (),
) -> Snapshot:
    """The caller owns the repeatable-read transaction; no display objects or Redis are read here."""
    row = (
        await session.scalars(
            select(ScheduleRow).where(
                ScheduleRow.workspace_id == workspace_id,
                ScheduleRow.session_id == session_id,
            )
        )
    ).one()
    recent = (
        await session.scalars(
            select(OccurrenceRow)
            .where(OccurrenceRow.schedule_id == row.id)
            .order_by(OccurrenceRow.created_at.desc(), OccurrenceRow.id.desc())
            .limit(history_limit)
        )
    ).all()
    unfinished = (
        await session.scalars(
            select(OccurrenceRow).where(
                OccurrenceRow.schedule_id == row.id,
                or_(OccurrenceRow.status == "pending", unfinished_occurrence()),
            )
        )
    ).all()
    occurrences = {item.id: item for item in (*recent, *unfinished)}
    departed = (
        (
            await session.scalars(
                select(OccurrenceRow).where(
                    OccurrenceRow.schedule_id == row.id,
                    OccurrenceRow.id.in_(set(previous_occurrences) - occurrences.keys()),
                )
            )
        ).all()
        if previous_occurrences
        else []
    )
    roots = [item.thread_id for item in recent if item.thread_id is not None]
    thread_ids = list(
        await session.scalars(
            select(ThreadRow.id)
            .where(
                ThreadRow.workspace_id == workspace_id,
                ThreadRow.session_id == session_id,
                or_(ThreadRow.id.in_(roots), unfinished_thread()),
            )
            .order_by(ThreadRow.created_at, ThreadRow.id)
        )
    )
    return Snapshot(
        schedule=ScheduleEvent(
            session_id=session_id,
            schedule_id=row.id,
            configuration=Configuration.model_validate(
                dict(
                    id=row.id,
                    session_id=session_id,
                    name=row.name,
                    state=row.state,
                    pause_reason=row.pause_reason,
                    version=row.version,
                    principal_id=row.principal_id,
                    schedule=CronSchedule(expression=row.cron_expression, timezone=row.timezone),
                    max_lateness_seconds=row.max_lateness_seconds,
                )
            ),
            progress=Progress(
                next_fire_at=row.next_fire_at,
                pending_occurrence_id=row.pending_occurrence_id,
                last_occurrence_id=row.last_occurrence_id,
            ),
        ),
        occurrences=[
            OccurrenceEvent(
                session_id=session_id, schedule_id=row.id, occurrence=OccurrenceSummary.model_validate(item)
            )
            for item in occurrences.values()
        ],
        thread_ids=thread_ids,
        departing_occurrences=[
            OccurrenceEvent(
                session_id=session_id, schedule_id=row.id, occurrence=OccurrenceSummary.model_validate(item)
            )
            for item in departed
        ],
    )
