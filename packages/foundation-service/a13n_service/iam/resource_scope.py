"""Organization-owned configuration and its visibility from consuming Workspaces."""

from dataclasses import dataclass

from sqlalchemy import ColumnElement, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from .authorization import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_organization_admin,
    authorize_workspace,
)
from .models import WorkspaceRecord


@dataclass(frozen=True, slots=True)
class ResourceScope:
    organization_id: str
    workspace_id: str | None

    @property
    def id(self) -> str:
        return self.workspace_id or self.organization_id

    def visible(
        self, organization: InstrumentedAttribute[str], workspace: InstrumentedAttribute[str | None]
    ) -> ColumnElement[bool]:
        return and_(organization == self.organization_id, visible_workspace(workspace, self.workspace_id))

    def accessible(
        self, organization: InstrumentedAttribute[str], workspace: InstrumentedAttribute[str | None]
    ) -> ColumnElement[bool]:
        if self.workspace_id is None:
            return organization == self.organization_id
        return self.visible(organization, workspace)

    def contains(self, organization_id: str, workspace_id: str | None) -> bool:
        return organization_id == self.organization_id and (workspace_id is None or workspace_id == self.workspace_id)


def visible_workspace(column: InstrumentedAttribute[str | None], workspace_id: str | None) -> ColumnElement[bool]:
    return or_(column.is_(None), column == workspace_id)


async def actor_scope(session: AsyncSession, actor: AuthenticatedActor) -> ResourceScope:
    """Resolve the credential boundary before querying tenant-owned resources."""
    if actor.boundary_workspace_id is not None:
        workspace = await session.get(WorkspaceRecord, actor.boundary_workspace_id)
        if workspace is None or workspace.deleted_at is not None:
            raise AuthorizationError("workspace_not_found", concealed=True)
        return ResourceScope(workspace.organization_id, workspace.id)
    if actor.boundary_organization_id is None:
        raise AuthorizationError("credential_boundary_mismatch", concealed=True)
    return ResourceScope(actor.boundary_organization_id, None)


async def authorize_scope(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str | None,
    action: WorkspaceAction,
) -> ResourceScope:
    if workspace_id is not None:
        workspace = await authorize_workspace(session, actor=actor, workspace_id=workspace_id, action=action)
        return ResourceScope(workspace.organization_id, workspace.workspace_id)
    organization_id = await authorize_organization_admin(session, actor=actor)
    return ResourceScope(organization_id, None)


async def authorize_resource(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str | None,
    action: WorkspaceAction,
    manage: bool = False,
) -> None:
    boundary = await actor_scope(session, actor)
    if not boundary.contains(organization_id, workspace_id) and boundary.workspace_id is not None:
        raise AuthorizationError("resource_not_found", concealed=True)
    if boundary.organization_id != organization_id:
        raise AuthorizationError("resource_not_found", concealed=True)
    target = workspace_id if manage or boundary.workspace_id is None else boundary.workspace_id
    await authorize_scope(session, actor=actor, workspace_id=target, action=action)
