"""Aggregate usage condition, evaluated while holding the Environment row lock."""

from datetime import datetime
from typing import Literal

from sqlalchemy import SQLColumnExpression, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Exists

from a13n_service.interactions.models import RunRecord

from .models import EnvironmentRecord
from .mount_models import RunEnvironmentMountRecord

type RetentionCondition = Literal["active", "idle"]


def active_use_exists(environment_id: str | SQLColumnExpression[str]) -> Exists:
    additional_use = (
        select(RunEnvironmentMountRecord.run_id)
        .where(
            RunEnvironmentMountRecord.run_id == RunRecord.id,
            RunEnvironmentMountRecord.environment_id == environment_id,
            RunEnvironmentMountRecord.use_started_at.is_not(None),
        )
        .correlate_except(RunEnvironmentMountRecord)
        .exists()
    )
    return (
        select(RunRecord.id)
        .where(
            RunRecord.status == "running",
            or_(
                (RunRecord.environment_id == environment_id) & RunRecord.environment_use_started_at.is_not(None),
                additional_use,
            ),
        )
        .exists()
    )


async def has_active_use(session: AsyncSession, environment_id: str) -> bool:
    return bool(await session.scalar(select(active_use_exists(environment_id))))


async def usage_condition(session: AsyncSession, environment: EnvironmentRecord) -> RetentionCondition:
    return "active" if await has_active_use(session, environment.id) else "idle"


async def refresh_retention(session: AsyncSession, environment: EnvironmentRecord, now: datetime) -> RetentionCondition:
    await session.flush()
    condition = await usage_condition(session, environment)
    if condition != environment.retention_condition:
        environment.retention_condition = condition
        environment.condition_since = now
        environment.next_maintenance_at = now
    return condition
