"""Aggregate usage condition, evaluated while holding the Environment row lock."""

from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.interactions.models import RunRecord, ThreadRecord

from .models import EnvironmentRecord

type RetentionCondition = Literal["active", "idle", "waiting_approval"]


async def has_active_use(session: AsyncSession, environment_id: str) -> bool:
    return (
        await session.scalar(
            select(RunRecord.id)
            .where(
                RunRecord.environment_id == environment_id,
                RunRecord.status == "running",
                RunRecord.environment_use_started_at.is_not(None),
            )
            .limit(1)
        )
        is not None
    )


async def usage_condition(session: AsyncSession, environment: EnvironmentRecord) -> RetentionCondition:
    if await has_active_use(session, environment.id):
        return "active"
    waiting = tuple(
        await session.scalars(
            select(RunRecord)
            .join(ThreadRecord, ThreadRecord.current_run_id == RunRecord.id)
            .where(
                RunRecord.environment_id == environment.id,
                RunRecord.status == "waiting",
                RunRecord.wait_reason.in_(("approval", "multiple")),
                RunRecord.environment_use_started_at.is_not(None),
            )
        )
    )
    resources = (run.to_resource() for run in waiting)
    approvals = tuple(
        run
        for run in resources
        if run.pending is not None and any(call.kind == "approval" for call in run.pending.calls)
    )
    if approvals:
        return "waiting_approval"
    return "idle"


async def refresh_retention(session: AsyncSession, environment: EnvironmentRecord, now: datetime) -> RetentionCondition:
    await session.flush()
    condition = await usage_condition(session, environment)
    if condition != environment.retention_condition:
        environment.retention_condition = condition
        environment.condition_since = now
    environment.next_maintenance_at = now
    return condition
