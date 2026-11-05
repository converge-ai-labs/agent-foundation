"""Environment lookup, private ownership and mutation preconditions shared by access paths."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.audit import record
from a13n_service.infra.errors import ServiceError, not_found
from a13n_service.infra.http import require_match
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope, allowed_verbs


def audit(session: AsyncSession, actor: Principal, environment: EnvironmentRow, verb: str) -> None:
    record(
        session,
        WorkspaceScope(environment.organization_id, environment.workspace_id),
        actor_id=actor.id,
        action=f"environment.{verb}",
        target_kind="environment",
        target_id=environment.id,
    )


async def find(
    session: AsyncSession, scope: WorkspaceScope, environment_id: str, *, lock: bool = False
) -> EnvironmentRow:
    query = select(EnvironmentRow).where(
        EnvironmentRow.workspace_id == scope.workspace_id, EnvironmentRow.id == environment_id
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = await session.scalar(query)
    if row is None:
        raise not_found("environment", environment_id)
    return row


def require_manager(actor: Principal, scope: WorkspaceScope, environment: EnvironmentRow) -> None:
    """A private external target is managed by its owner, or by a workspace administrator."""
    if environment.owner_principal_id not in {None, actor.id} and "admin" not in allowed_verbs(actor, scope):
        raise ServiceError("forbidden", "Private environments are managed by their owner", {"verb": "write"})


async def writable(
    session: AsyncSession,
    actor: Principal,
    workspace_id: str,
    environment_id: str,
    if_match: str | None,
    *,
    lock: bool = True,
) -> EnvironmentRow:
    """The instance a write names, once its precondition holds and the actor may manage it."""
    scope = await workspace_scope(session, actor, workspace_id, "write")
    environment = await find(session, scope, environment_id, lock=lock)
    require_match(if_match, environment.id, environment.version)
    require_manager(actor, scope, environment)
    return environment
