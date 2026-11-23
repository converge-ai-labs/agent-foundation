"""Workspace policy shared by App SDK writes."""

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import ServiceError, not_found
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.access import lock_organization, principal_for, refuse_archived
from a13n_service.tenancy.app_plugins import policy
from a13n_service.tenancy.authorize import Principal, WorkspaceScope, authorize
from a13n_service.tenancy.tables import OrganizationRow, WorkspaceRow


def require_transaction(session: AsyncSession) -> None:
    if not session.info.get("a13n.transaction") or not session.in_transaction() or session.in_nested_transaction():
        raise RuntimeError("App SDK writes require the outer Service transaction")


async def app_workspace(
    session: AsyncSession,
    runtime: Runtime,
    workspace_id: str,
    app_id: str,
    *,
    write: bool = True,
) -> WorkspaceScope:
    runtime.apps.require(app_id)
    workspace = await session.get(WorkspaceRow, workspace_id)
    if workspace is None:
        raise not_found("workspace", workspace_id)
    scope = WorkspaceScope(workspace.organization_id, workspace_id)
    if write:
        require_transaction(session)
        await lock_organization(session, scope.organization_id)
        await session.refresh(workspace)
        refuse_archived(workspace)
    organization = await session.get_one(OrganizationRow, scope.organization_id, populate_existing=True)
    if app_id not in policy(organization.settings).enabled:
        raise ServiceError("disabled", "App plugin is disabled for this organization", {"app_id": app_id})
    return scope


async def app_actor(
    session: AsyncSession, runtime: Runtime, scope: WorkspaceScope, actor_id: str, *, share: bool = True
) -> Principal:
    actor = await principal_for(session, runtime.access, actor_id, confinement=scope, share=share)
    if not any(grant.organization_id == scope.organization_id for grant in actor.grants):
        raise not_found("workspace", scope.workspace_id)
    return actor


async def run_actor(
    session: AsyncSession, runtime: Runtime, scope: WorkspaceScope, principal_id: str, *, share: bool = True
) -> Principal:
    actor = await app_actor(session, runtime, scope, principal_id, share=share)
    if actor.kind != "service_account":
        raise ServiceError("forbidden", "App execution requires a workspace Service Account")
    authorize(actor, scope, "run")
    return actor
