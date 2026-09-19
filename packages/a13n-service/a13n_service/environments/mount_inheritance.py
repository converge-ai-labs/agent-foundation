"""Retained execution copies accepted mounts, never live use or loading evidence."""

from sqlalchemy import insert, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.interactions.domain import Run, RunInputKind

from .mount_models import RunEnvironmentMountRecord


async def inherit_run_mounts(database: AsyncSession, *, run: Run, workspace_id: str) -> None:
    """Join the acceptance transaction after its retained lineage has been validated."""
    source_id = run.retry_of_run_id
    if source_id is None and run.input_kind in {RunInputKind.waiting_feedback, RunInputKind.waiting_continue}:
        source_id = run.parent_run_id
    if source_id is None:
        return
    mount = RunEnvironmentMountRecord
    # Preserve original acceptance order and provenance. Nullable use/observation
    # columns start empty; every successor acquires fresh Attempt-scoped use.
    await database.execute(
        insert(mount).from_select(
            [
                "run_id",
                "name",
                "organization_id",
                "workspace_id",
                "environment_id",
                "working_directory",
                "created_at",
                "principal_type",
                "principal_id",
                "application_status",
            ],
            select(
                literal(run.id),
                mount.name,
                mount.organization_id,
                mount.workspace_id,
                mount.environment_id,
                mount.working_directory,
                mount.created_at,
                mount.principal_type,
                mount.principal_id,
                literal("pending"),
            ).where(
                mount.run_id == source_id,
                mount.organization_id == run.organization_id,
                mount.workspace_id == workspace_id,
            ),
        )
    )
