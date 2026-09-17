"""Canonical Attempt-authorized Environment use and retained first-use evidence."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam.authorization import WorkspaceAction, authorize_persisted_agent_principal_actions
from a13n_service.interactions.models import RunRecord

from .capacity import CapacityLimits
from .models import EnvironmentProviderRecord, EnvironmentRecord

if TYPE_CHECKING:
    from a13n_service.interactions.attempts import AttemptContext


async def lock_run_environment_use(
    session: AsyncSession,
    environment_id: str,
    attempt: AttemptContext,
    capacity: CapacityLimits,
    now: datetime,
) -> tuple[RunRecord, EnvironmentRecord, EnvironmentProviderRecord]:
    from a13n_service.interactions.attempts import lock_attempt_authority

    run, _, _ = await lock_attempt_authority(session, attempt, now)
    if run.environment_id != environment_id:
        raise ValueError("Environment is not the Run's accepted selection")
    await capacity.lock_workspace(session, environment_id)
    row = await session.get(EnvironmentRecord, environment_id, with_for_update=True)
    if row is None:
        raise ValueError("Environment is unavailable")
    provider = await session.get(EnvironmentProviderRecord, row.provider_id)
    if provider is None:
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
    return run, row, provider


def mark_run_environment_use(run: RunRecord, environment: EnvironmentRecord, now: datetime) -> None:
    run.environment_use_started_at = run.environment_use_started_at or now
    if environment.retention_condition != "active":
        environment.retention_condition = "active"
        environment.condition_since = now
