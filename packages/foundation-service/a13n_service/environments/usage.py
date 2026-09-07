"""Run selections and Environment usage share the existing Run transaction."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.interactions.models import RunRecord

from .errors import invalid_environment
from .models import EnvironmentRecord
from .retention import refresh_retention


async def refresh_run_retention(database: AsyncSession, *, run: RunRecord, now: datetime) -> None:
    """Refresh aggregate retention after the complete Run status change, before inbox locks."""
    if run.environment_id is None or run.environment_use_started_at is None:
        return
    environment = await database.scalar(
        select(EnvironmentRecord).where(EnvironmentRecord.id == run.environment_id).with_for_update()
    )
    if environment is None:
        raise invalid_environment("Run Environment is missing")
    await refresh_retention(database, environment, now)


async def lock_run_environments(
    database: AsyncSession, *, run: RunRecord, additional_environment_ids: tuple[str, ...] = ()
) -> None:
    ids = sorted(set(additional_environment_ids + ((run.environment_id,) if run.environment_id else ())))
    if ids:
        rows = tuple(
            await database.scalars(
                select(EnvironmentRecord)
                .where(EnvironmentRecord.id.in_(ids))
                .order_by(EnvironmentRecord.id)
                .with_for_update()
            )
        )
        if [row.id for row in rows] != ids:
            raise invalid_environment("Run references a missing Environment")
