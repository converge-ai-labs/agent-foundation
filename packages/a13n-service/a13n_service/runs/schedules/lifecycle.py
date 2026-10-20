"""ScheduleTrigger records and timeline changes inside the caller's locked Schedule transaction."""

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.ids import new_object_id
from a13n_service.runs.schedules.calendar import TIMEZONE_DATA_VERSION, Calendar
from a13n_service.runs.schedules.schemas import (
    CronSchedule,
    OccurrenceSnapshot,
    OccurrenceStatus,
    OnceSchedule,
    ScheduleInvocation,
    ScheduleRule,
    ScheduleTrigger,
)
from a13n_service.runs.schedules.tables import OccurrenceRow, ScheduleRow
from a13n_service.tenancy.authorize import ExecutionAuthority


def rule(row: ScheduleRow) -> ScheduleRule:
    if row.schedule_type == "once":
        assert row.run_at is not None
        return OnceSchedule(type="once", run_at=row.run_at)
    assert row.cron_expression is not None and row.timezone is not None
    return CronSchedule(expression=row.cron_expression, timezone=row.timezone)


def calendar(row: ScheduleRow) -> Calendar:
    assert row.cron_expression is not None and row.timezone is not None
    return Calendar.parse(row.cron_expression, row.timezone)


def next_fire(row: ScheduleRow, current: datetime) -> datetime | None:
    after = max(current, row.last_processed_fire_at) if row.last_processed_fire_at else current
    if row.schedule_type == "once":
        return row.run_at if row.run_at is not None and row.run_at > after else None
    return calendar(row).next(after)


def latest_due(row: ScheduleRow, current: datetime) -> datetime | None:
    if row.next_fire_at is None or row.next_fire_at > current:
        return None
    if row.schedule_type == "once":
        return row.next_fire_at
    return calendar(row).previous(current + timedelta(microseconds=1))


async def occurrence(
    session: AsyncSession,
    row: ScheduleRow,
    current: datetime,
    *,
    trigger: ScheduleTrigger,
    scheduled_at: datetime | None = None,
    status: OccurrenceStatus = "pending",
    reason: str | None = None,
    requested_by_id: str | None = None,
    request_key: str | None = None,
    request_digest: str | None = None,
    authority: ExecutionAuthority | None = None,
) -> OccurrenceRow:
    offset = (
        scheduled_at.astimezone(calendar(row).timezone).utcoffset()
        if scheduled_at and row.schedule_type == "cron"
        else None
    )
    snapshot = OccurrenceSnapshot(
        name=row.name,
        schedule=rule(row),
        max_lateness_seconds=row.max_lateness_seconds,
        invocation=ScheduleInvocation.model_validate(row.invocation),
        timezone_data_version=TIMEZONE_DATA_VERSION,
        utc_offset_seconds=int(offset.total_seconds()) if offset is not None else None,
    )
    result = OccurrenceRow(
        id=new_object_id("occ"),
        organization_id=row.organization_id,
        workspace_id=row.workspace_id,
        schedule_id=row.id,
        session_id=row.session_id,
        trigger=trigger,
        scheduled_at=scheduled_at,
        covered_from_at=row.next_fire_at if scheduled_at else None,
        schedule_version=row.version,
        snapshot=snapshot.model_dump(mode="json"),
        principal_id=row.principal_id,
        authority=row.authority if authority is None else authority.model_dump(mode="json"),
        requested_by_id=requested_by_id,
        request_key=request_key,
        request_digest=request_digest,
        status=status,
        reason=reason,
        deadline_at=(scheduled_at or current) + timedelta(seconds=row.max_lateness_seconds),
        dispatch_attempts=0,
        next_attempt_at=current if status == "pending" else None,
        finished_at=None if status == "pending" else current,
    )
    session.add(result)
    await session.flush()
    row.last_occurrence_id = result.id
    if status == "pending":
        row.pending_occurrence_id = result.id
    if scheduled_at is not None:
        row.last_processed_fire_at = scheduled_at
    return result


def complete_once(row: ScheduleRow, item: OccurrenceRow) -> None:
    """Completion concerns automatic submission, never the resulting Run or manual dispatch."""
    if (
        row.schedule_type == "once"
        and row.state == "enabled"
        and item.trigger == "scheduled"
        and item.status in {"submitted", "skipped", "failed"}
    ):
        row.state, row.pause_reason, row.next_fire_at = "completed", None, None


def finish(
    row: ScheduleRow, item: OccurrenceRow, current: datetime, *, status: OccurrenceStatus, reason: str | None
) -> None:
    item.status, item.reason, item.finished_at = status, reason, current
    item.next_attempt_at = None
    if row.pending_occurrence_id == item.id:
        row.pending_occurrence_id = None
    complete_once(row, item)


async def cancel_pending(session: AsyncSession, row: ScheduleRow, current: datetime, reason: str) -> str | None:
    if row.pending_occurrence_id is None:
        return None
    item = await session.get_one(OccurrenceRow, row.pending_occurrence_id)
    finish(row, item, current, status="cancelled", reason=reason)
    await session.flush()
    return item.id


async def close_due(session: AsyncSession, row: ScheduleRow, current: datetime, reason: str) -> None:
    """Before replacing a timeline, retain one skipped record for its unprocessed elapsed range."""
    due = latest_due(row, current)
    if due is not None:
        await occurrence(session, row, current, trigger="scheduled", scheduled_at=due, status="skipped", reason=reason)
