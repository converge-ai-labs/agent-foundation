"""Request-local IAM authorization for Workspace-owned resources."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import PrincipalRef, PrincipalType
from .models import RoleBindingRecord, ServiceAccountRecord, UserRecord, WorkspaceRecord


class WorkspaceAction(StrEnum):
    models_read = "models.read"
    models_manage = "models.manage"
    secrets_read = "secrets.read"
    secrets_manage = "secrets.manage"
    connector_read = "connector.read"
    connector_create = "connector.create"
    connector_configure = "connector.configure"
    connector_invoke = "connector.invoke"
    connection_read = "connection.read"
    connection_manage = "connection.manage"
    trigger_read = "trigger.read"
    trigger_configure = "trigger.configure"


_WORKSPACE_READ_ACTIONS = frozenset(
    {
        WorkspaceAction.models_read,
        WorkspaceAction.secrets_read,
        WorkspaceAction.connector_read,
        WorkspaceAction.connection_read,
        WorkspaceAction.trigger_read,
    }
)

_WORKSPACE_ROLE_ACTIONS: dict[str, frozenset[WorkspaceAction]] = {
    "viewer": _WORKSPACE_READ_ACTIONS,
    "runner": _WORKSPACE_READ_ACTIONS | {WorkspaceAction.connector_invoke},
    "builder": frozenset(WorkspaceAction),
    "admin": frozenset(WorkspaceAction),
}


@dataclass(frozen=True, slots=True)
class AuthenticatedActor:
    principal: PrincipalRef
    auth_method: str
    credential_id: str
    boundary_workspace_id: str
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class AuthorizedWorkspace:
    organization_id: str
    workspace_id: str
    actor: AuthenticatedActor


class AuthorizationError(Exception):
    def __init__(self, code: str, *, concealed: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.concealed = concealed


async def authorize_workspace(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    action: WorkspaceAction,
) -> AuthorizedWorkspace:
    """Authorize one operation from current Principal, credential boundary, and grants."""

    if actor.boundary_workspace_id != workspace_id:
        raise AuthorizationError("credential_boundary_mismatch", concealed=True)

    workspace = await session.scalar(
        select(WorkspaceRecord).where(WorkspaceRecord.id == workspace_id, WorkspaceRecord.deleted_at.is_(None))
    )
    if workspace is None:
        raise AuthorizationError("workspace_not_found", concealed=True)

    if actor.principal.principal_type is PrincipalType.user:
        await _require_active_user(session, actor.principal.principal_id)
    else:
        await _require_active_service_account(session, actor.principal.principal_id, workspace)

    bindings = tuple((await session.scalars(_binding_query(actor.principal, workspace))).all())
    if actor.principal.principal_type is PrincipalType.user and not any(
        item.resource_type == "organization" for item in bindings
    ):
        raise AuthorizationError("organization_membership_required", concealed=True)

    permissions: set[WorkspaceAction] = set()
    for binding in bindings:
        if binding.resource_type == "organization" and binding.role_key == "admin":
            permissions.update(frozenset(WorkspaceAction))
        elif binding.resource_type == "workspace":
            permissions.update(_WORKSPACE_ROLE_ACTIONS.get(binding.role_key, ()))
    if action not in permissions:
        raise AuthorizationError("permission_denied", concealed=True)
    return AuthorizedWorkspace(organization_id=workspace.organization_id, workspace_id=workspace.id, actor=actor)


async def _require_active_user(session: AsyncSession, user_id: str) -> None:
    status = await session.scalar(select(UserRecord.status).where(UserRecord.id == user_id))
    if status != "active":
        raise AuthorizationError("principal_inactive")


async def _require_active_service_account(session: AsyncSession, principal_id: str, workspace: WorkspaceRecord) -> None:
    account = await session.scalar(
        select(ServiceAccountRecord).where(
            ServiceAccountRecord.id == principal_id,
            ServiceAccountRecord.organization_id == workspace.organization_id,
            ServiceAccountRecord.workspace_id == workspace.id,
            ServiceAccountRecord.status == "active",
            ServiceAccountRecord.deleted_at.is_(None),
        )
    )
    if account is None:
        raise AuthorizationError("principal_inactive")


def _binding_query(principal: PrincipalRef, workspace: WorkspaceRecord) -> Select[tuple[RoleBindingRecord]]:
    return select(RoleBindingRecord).where(
        RoleBindingRecord.organization_id == workspace.organization_id,
        RoleBindingRecord.principal_type == principal.principal_type.value,
        RoleBindingRecord.principal_id == principal.principal_id,
        or_(
            and_(
                RoleBindingRecord.resource_type == "organization",
                RoleBindingRecord.resource_id == workspace.organization_id,
            ),
            and_(
                RoleBindingRecord.resource_type == "workspace",
                RoleBindingRecord.resource_id == workspace.id,
                RoleBindingRecord.workspace_id == workspace.id,
            ),
        ),
    )
