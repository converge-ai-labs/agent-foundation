"""Run selections and Environment usage share the existing Run transaction."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.interactions.models import RunRecord

from .errors import invalid_environment
from .models import EnvironmentRecord
from .mount_models import RunEnvironmentMountRecord
from .retention import refresh_retention


async def refresh_run_retention(database: AsyncSession, *, run: RunRecord, now: datetime) -> None:
    """Refresh aggregate retention after the complete Run status change, before inbox locks."""
    used_ids = set(
        await database.scalars(
            select(RunEnvironmentMountRecord.environment_id).where(
                RunEnvironmentMountRecord.run_id == run.id,
                RunEnvironmentMountRecord.use_started_at.is_not(None),
            )
        )
    )
    if run.environment_id is not None and run.environment_use_started_at is not None:
        used_ids.add(run.environment_id)
    if used_ids:
        environments = tuple(
            await database.scalars(
                select(EnvironmentRecord)
                .where(EnvironmentRecord.id.in_(used_ids))
                .order_by(EnvironmentRecord.id)
                .with_for_update()
            )
        )
        if len(environments) != len(used_ids):
            raise invalid_environment("Run Environment is missing")
        for environment in environments:
            await refresh_retention(database, environment, now)


async def lock_run_environments(
    database: AsyncSession,
    *,
    run: RunRecord,
    additional_environment_ids: tuple[str, ...] = (),
    mounted_environment_ids: tuple[str, ...] | None = None,
) -> None:
    mount_ids = (
        mounted_environment_ids
        if mounted_environment_ids is not None
        else tuple(
            await database.scalars(
                select(RunEnvironmentMountRecord.environment_id).where(RunEnvironmentMountRecord.run_id == run.id)
            )
        )
    )
    ids = sorted(set(additional_environment_ids + mount_ids + ((run.environment_id,) if run.environment_id else ())))
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
