"""Bounded PostgreSQL bootstrap observations, fenced by instance identity and generation."""

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
    # One parent lock serializes different cwd writers and prevents resurrection after soft deletion.
    environment = await lock(session, EnvironmentRow, environment_id)
    if environment is None or environment.status in {"deleted", "deleting"} or environment.generation != generation:
        return
    previous = await session.get(SnapshotRow, (environment_id, working_directory))
    if previous is not None and previous.environment_generation == generation:
        old = EnvironmentBootstrapSnapshot.model_validate(previous.snapshot)
        files = {(item.path, item.limits): item for item in old.files}
        for item in snapshot.files:
            # Absence applies to every read profile, and fresh presence supersedes prior absence.
            files = {
                key: value
                for key, value in files.items()
                if value.path != item.path or (item.status != "absent" and value.status != "absent")
            }
            files[item.path, item.limits] = item
        outlines = {item.profile: item for item in (*old.workspace_outline, *snapshot.workspace_outline)}
        merged = EnvironmentBootstrapSnapshot(
            files=tuple(files.values()),
            project_guidance=snapshot.project_guidance or old.project_guidance,
            workspace_outline=tuple(outlines.values()),
        )
        if len(merged.model_dump_json().encode()) <= MAX_BYTES:
            snapshot = merged
    if len(snapshot.model_dump_json().encode()) > MAX_BYTES:
        # Advance the fence with an unknown observation rather than serving stale data.
        snapshot = EnvironmentBootstrapSnapshot()
    stmt = insert(SnapshotRow).values(
        environment_id=environment_id,
        working_directory=working_directory,
        organization_id=environment.organization_id,
        workspace_id=environment.workspace_id,
        environment_generation=generation,
        snapshot=snapshot.model_dump(mode="json"),
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
