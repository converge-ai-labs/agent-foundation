"""Schedule and Occurrence state added to the ordinary Session stream."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from a13n_service.runs.schedules.lifecycle import rule
from a13n_service.runs.schedules.schemas import OccurrenceSummary, ScheduleRule, ScheduleState
from a13n_service.runs.schedules.tables import OccurrenceRow, ScheduleRow
from a13n_service.runs.session_state import Scope


class Configuration(BaseModel):
    id: str
    session_id: str
    name: str
    state: ScheduleState
    pause_reason: str | None
    version: int
    principal_id: str
    schedule: ScheduleRule
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


async def snapshots(session: AsyncSession, scopes: Sequence[Scope]) -> dict[Scope, Snapshot]:
    """Read compact state for all watched Sessions in the caller's read-only transaction."""
    schedules = (
        await session.scalars(
            select(ScheduleRow)
            .options(defer(ScheduleRow.invocation), defer(ScheduleRow.authority))
            .where(
                tuple_(ScheduleRow.workspace_id, ScheduleRow.session_id).in_(
                    [(scope.workspace_id, scope.session_id) for scope in scopes]
                )
            )
        )
    ).all()
    if not schedules:
        return {}
    occurrences = (
        await session.scalars(
            select(OccurrenceRow)
            .options(defer(OccurrenceRow.snapshot), defer(OccurrenceRow.authority))
            .where(OccurrenceRow.schedule_id.in_([row.id for row in schedules]))
            .order_by(OccurrenceRow.created_at, OccurrenceRow.id)
        )
    ).all()
    grouped: dict[str, list[OccurrenceEvent]] = {row.id: [] for row in schedules}
    for item in occurrences:
        grouped[item.schedule_id].append(
            OccurrenceEvent(
                session_id=item.session_id,
                schedule_id=item.schedule_id,
                occurrence=OccurrenceSummary.model_validate(item),
            )
        )
    return {
        Scope(row.workspace_id, row.session_id): Snapshot(
            schedule=ScheduleEvent(
                session_id=row.session_id,
                schedule_id=row.id,
                configuration=Configuration.model_validate(
                    dict(
                        id=row.id,
                        name=row.name,
                        state=row.state,
                        pause_reason=row.pause_reason,
                        version=row.version,
                        session_id=row.session_id,
                        principal_id=row.principal_id,
                        schedule=rule(row),
                        max_lateness_seconds=row.max_lateness_seconds,
                    )
                ),
                progress=Progress(
                    next_fire_at=row.next_fire_at,
                    pending_occurrence_id=row.pending_occurrence_id,
                    last_occurrence_id=row.last_occurrence_id,
                ),
            ),
            occurrences=grouped[row.id],
        )
        for row in schedules
    }
