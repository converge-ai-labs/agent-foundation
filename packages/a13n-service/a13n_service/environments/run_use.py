"""Canonical Attempt-authorized Environment use and retained first-use evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam.authorization import WorkspaceAction, authorize_persisted_agent_principal_actions
from a13n_service.interactions.models import RunRecord, SessionRecord

from .capacity import CapacityLimits
from .models import EnvironmentProviderRecord, EnvironmentRecord
from .mount_models import RunEnvironmentMountRecord

if TYPE_CHECKING:
    from a13n_service.interactions.attempts import AttemptContext


@dataclass(frozen=True, slots=True)
class RunEnvironmentBinding:
    """One accepted binding and the row which owns its retained first-use evidence."""

    name: str
    environment_id: str
    workspace_id: str
    access: str
    record: RunRecord | RunEnvironmentMountRecord


async def load_run_environment_binding(
    session: AsyncSession, run: RunRecord, *, name: str = "workspace", for_update: bool = False
) -> RunEnvironmentBinding | None:
    record: RunRecord | RunEnvironmentMountRecord
    if name == "workspace":
        if run.environment_id is None:
            return None
        if run.environment_access is None:
            raise ValueError("Run Environment access is missing")
        record, environment_id, access = run, run.environment_id, run.environment_access
    else:
        mount = await session.get(RunEnvironmentMountRecord, (run.id, name), with_for_update=for_update)
        if mount is None or mount.organization_id != run.organization_id:
            raise ValueError("The accepted Run mount is unavailable")
        record, environment_id, access = mount, mount.environment_id, mount.access
    workspace_id = await session.scalar(select(SessionRecord.workspace_id).where(SessionRecord.id == run.session_id))
    if workspace_id is None or (isinstance(record, RunEnvironmentMountRecord) and record.workspace_id != workspace_id):
        raise ValueError("The accepted Environment binding has no matching Workspace")
    return RunEnvironmentBinding(name, environment_id, workspace_id, access, record)


async def lock_run_environment_use(
    session: AsyncSession,
    environment_id: str,
    attempt: AttemptContext,
    capacity: CapacityLimits,
    now: datetime,
    *,
    mount_name: str = "workspace",
) -> tuple[RunEnvironmentBinding, EnvironmentRecord, EnvironmentProviderRecord]:
    from a13n_service.interactions.attempts import lock_attempt_authority

    attempt.lease.require_current(now)
    run, _, _ = await lock_attempt_authority(session, attempt, now)
    binding = await load_run_environment_binding(session, run, name=mount_name, for_update=True)
    if binding is None or binding.environment_id != environment_id:
        raise ValueError("Environment is not the Run's accepted selection")
    await capacity.lock_workspace(session, environment_id)
    row = await session.get(EnvironmentRecord, environment_id, with_for_update=True)
    if row is None or (row.organization_id, row.workspace_id) != (run.organization_id, binding.workspace_id):
        raise ValueError("Environment is unavailable")
    if row.ownership == "external" and row.status == "deleted":
        raise ValueError("External Environment was removed")
    provider = await session.get(EnvironmentProviderRecord, row.provider_id)
    if (
        provider is None
        or provider.organization_id != row.organization_id
        or provider.workspace_id not in {None, row.workspace_id}
    ):
        raise ValueError("Environment Provider is unavailable")
    await authorize_persisted_agent_principal_actions(
        session,
        principal=run.to_resource().authority_principal,
        organization_id=row.organization_id,
        workspace_id=row.workspace_id,
        agent_id=run.agent_id,
        actions=frozenset({WorkspaceAction.environment_use, WorkspaceAction.agent_invoke}),
        snapshot=attempt.authorization.snapshot,
    )
    if not provider.enabled:
        raise ValueError("Environment Provider is disabled")
    return binding, row, provider


def mark_run_environment_use(binding: RunEnvironmentBinding, environment: EnvironmentRecord, now: datetime) -> None:
    record = binding.record
    if isinstance(record, RunRecord):
        record.environment_use_started_at = record.environment_use_started_at or now
    else:
        record.use_started_at = record.use_started_at or now
    if environment.retention_condition != "active":
        environment.retention_condition = "active"
        environment.condition_since = now
