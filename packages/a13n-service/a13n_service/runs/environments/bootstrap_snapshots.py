"""Bounded PostgreSQL bootstrap observations, fenced by instance identity and generation."""

import json
from datetime import datetime

from a13n_harness.environment import EnvironmentBootstrapSnapshot
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import lock
from a13n_service.resources.environment_instances.tables import EnvironmentBootstrapSnapshotRow as SnapshotRow
from a13n_service.resources.environment_instances.tables import EnvironmentRow

MAX_BYTES = 256 * 1024
MAX_DIRECTORIES = 8


async def save(
    session: AsyncSession,
    environment_id: str,
    working_directory: str,
    generation: int,
    observed_at: datetime,
    snapshot: EnvironmentBootstrapSnapshot,
) -> None:
    value = snapshot.model_dump(mode="json")
    if len(json.dumps(value).encode()) > MAX_BYTES:
        return
    # One parent lock serializes different cwd writers and prevents resurrection after soft deletion.
    environment = await lock(session, EnvironmentRow, environment_id)
    if environment is None or environment.status in {"deleted", "deleting"} or environment.generation != generation:
        return
    stmt = insert(SnapshotRow).values(
        environment_id=environment_id,
        working_directory=working_directory,
        organization_id=environment.organization_id,
        workspace_id=environment.workspace_id,
        environment_generation=generation,
        snapshot=value,
        observed_at=observed_at,
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[SnapshotRow.environment_id, SnapshotRow.working_directory],
            set_={key: getattr(stmt.excluded, key) for key in ("environment_generation", "snapshot", "observed_at")},
            where=or_(
                stmt.excluded.environment_generation > SnapshotRow.environment_generation,
                and_(
                    stmt.excluded.environment_generation == SnapshotRow.environment_generation,
                    stmt.excluded.observed_at >= SnapshotRow.observed_at,
                ),
            ),
        )
    )
    excess = (
        select(SnapshotRow.working_directory)
        .where(SnapshotRow.environment_id == environment_id)
        .order_by(SnapshotRow.observed_at.desc(), SnapshotRow.working_directory)
        .offset(MAX_DIRECTORIES)
    )
    await session.execute(
        delete(SnapshotRow).where(
            SnapshotRow.environment_id == environment_id, SnapshotRow.working_directory.in_(excess)
        )
    )


async def discard(session: AsyncSession, environment_id: str) -> None:
    # Lifecycle callers are applying the full tombstone transition in this transaction.
    with session.no_autoflush:
        await session.execute(delete(SnapshotRow).where(SnapshotRow.environment_id == environment_id))
