"""Control-role scans: persist one due trigger before separately submitting its input."""

import asyncio
from datetime import timedelta

from sqlalchemy import func, select

from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.sweeps import Sweep
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schedules import dispatch, lifecycle, queries
from a13n_service.runs.schedules.tables import OccurrenceRow, ScheduleRow


async def plan_next(runtime: Runtime) -> str | None:
    """Transaction A. Return the processed Schedule ID only after its intent and timeline have committed."""
    async with transaction(runtime.storage) as session:
        row = await session.scalar(
            select(ScheduleRow)
            .where(ScheduleRow.state == "enabled", ScheduleRow.next_fire_at <= func.clock_timestamp())
            .order_by(ScheduleRow.next_fire_at, ScheduleRow.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        current = await now(session)
        latest = lifecycle.latest_due(row, current)
        if latest is None:
            return row.id
        # Unique planned instants survive configuration changes and a retry after an unknown commit result.
        existing = await session.scalar(
            select(OccurrenceRow.id).where(
                OccurrenceRow.schedule_id == row.id,
                OccurrenceRow.trigger == "scheduled",
                OccurrenceRow.scheduled_at == latest,
            )
        )
        if existing is None and (row.last_processed_fire_at is None or latest > row.last_processed_fire_at):
            reason = None
            if current >= latest + timedelta(seconds=row.max_lateness_seconds):
                reason = "missed_deadline"
            elif row.pending_occurrence_id is not None or await queries.has_unfinished(session, row):
                reason = "overlap"
            item = await lifecycle.occurrence(
                session,
                runtime,
                row,
                current,
                trigger="scheduled",
                scheduled_at=latest,
                status="skipped" if reason else "pending",
                reason=reason,
            )
            await lifecycle.complete_once(session, runtime, row, item, current)
            dispatch.log_after_commit(session, row, item)
        row.last_processed_fire_at = max(row.last_processed_fire_at, latest) if row.last_processed_fire_at else latest
        row.next_fire_at = lifecycle.next_fire(row, current)
        await session.flush()
        return row.id


class Scheduler:
    def __init__(self, runtime: Runtime):
        self.runtime = runtime

    async def _due(self) -> None:
        for _ in range(self.runtime.settings.schedules.batch):
            schedule_id = await plan_next(self.runtime)
            if schedule_id is None:
                return
            # Transaction A has ended. B may fail or be interrupted without losing the durable trigger.
            await dispatch.process(self.runtime, schedule_id)

    async def _pending(self) -> None:
        async with short_session(self.runtime.storage) as session:
            due = func.least(OccurrenceRow.next_attempt_at, OccurrenceRow.deadline_at)
            ids = (
                await session.scalars(
                    select(OccurrenceRow.schedule_id)
                    .where(OccurrenceRow.status == "pending", due <= func.clock_timestamp())
                    .order_by(due, OccurrenceRow.id)
                    .limit(self.runtime.settings.schedules.batch)
                )
            ).all()
        for schedule_id in ids:
            await dispatch.process(self.runtime, schedule_id)

    async def __call__(self) -> None:
        # Separate batch allowances and tasks prevent either backlog from consuming the other's allowance.
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self._pending())
            tasks.create_task(self._due())


def schedule_sweep(runtime: Runtime) -> Sweep:
    return Sweep(
        name="dispatch_schedules", every=runtime.settings.schedules.scan_seconds, run=Scheduler(runtime), timeout=30
    )
