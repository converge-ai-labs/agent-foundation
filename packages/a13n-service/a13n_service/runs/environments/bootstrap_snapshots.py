"""Bounded PostgreSQL bootstrap observations, fenced by instance identity and generation."""

from datetime import datetime, timedelta

from a13n_harness.environment import EnvironmentBootstrapSnapshot
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import lock
from a13n_service.resources.environment_instances.tables import EnvironmentBootstrapSnapshotRow as SnapshotRow
from a13n_service.resources.environment_instances.tables import EnvironmentRow

MAX_BYTES = 256 * 1024
MAX_DIRECTORIES = 8
REFRESH_INTERVAL = timedelta(seconds=5)


def _normalize(snapshot: EnvironmentBootstrapSnapshot) -> EnvironmentBootstrapSnapshot:
    return EnvironmentBootstrapSnapshot(
        files=tuple(sorted(snapshot.files, key=lambda item: (item.path, item.limits))),
        project_guidance=snapshot.project_guidance,
        workspace_outline=tuple(sorted(snapshot.workspace_outline, key=lambda item: item.profile)),
    )


def _effective(
    previous: SnapshotRow | None, generation: int, snapshot: EnvironmentBootstrapSnapshot
) -> tuple[EnvironmentBootstrapSnapshot | None, EnvironmentBootstrapSnapshot]:
    old = None
    if previous is not None and previous.environment_generation == generation:
        try:
            old = _normalize(EnvironmentBootstrapSnapshot.model_validate(previous.snapshot))
        except ValueError:
            # Replace a malformed historical payload with the next admissible observation.
            pass
    if old is not None:
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
    if len(snapshot.model_dump_json().encode()) > MAX_BYTES or any("\x00" in item.text for item in snapshot.files):
        # An oversized capture or text PostgreSQL JSONB cannot represent still advances the fence.
        snapshot = EnvironmentBootstrapSnapshot()
    return old, _normalize(snapshot)


async def is_fresh(
    session: AsyncSession,
    environment_id: str,
    working_directory: str,
    generation: int,
    observed_at: datetime,
    snapshot: EnvironmentBootstrapSnapshot,
) -> bool:
    """Skip a snapshot write when the committed row already covers this observation."""
    previous = await session.get(SnapshotRow, (environment_id, working_directory))
    if previous is None or previous.environment_generation < generation:
        return False
    if previous.environment_generation > generation or observed_at < previous.observed_at:
        return True
    if observed_at - previous.observed_at >= REFRESH_INTERVAL:
        return False
    old, effective = _effective(previous, generation, snapshot)
    return old is not None and effective == old


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
    previous = await session.get(SnapshotRow, (environment_id, working_directory), populate_existing=True)
    if previous is not None:
        if previous.environment_generation > generation or (
            previous.environment_generation == generation and observed_at < previous.observed_at
        ):
            return
    old, snapshot = _effective(previous, generation, snapshot)
    if previous is not None and old is not None and snapshot == old:
        if observed_at - previous.observed_at >= REFRESH_INTERVAL:
            await session.execute(
                update(SnapshotRow)
                .where(
                    SnapshotRow.environment_id == environment_id,
                    SnapshotRow.working_directory == working_directory,
                    SnapshotRow.environment_generation == generation,
                    SnapshotRow.observed_at < observed_at,
                )
                .values(observed_at=observed_at)
            )
        return
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
    if previous is not None:
        return
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
