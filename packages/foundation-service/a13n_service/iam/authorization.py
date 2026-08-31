"""Request-local IAM authorization for Workspace-owned resources."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import PrincipalRef, PrincipalType
from .models import RoleBindingRecord, ServiceAccountRecord, UserRecord, WorkspaceRecord


class WorkspaceAction(StrEnum):
    asset_read = "asset.read"
    asset_create = "asset.create"
    asset_use = "asset.use"
    asset_delete = "asset.delete"
    models_read = "models.read"
    models_manage = "models.manage"
    secrets_read = "secrets.read"
    secrets_manage = "secrets.manage"
    skill_read = "skill.read"
    skill_create = "skill.create"
    skill_update = "skill.update"
    skill_delete = "skill.delete"
    skill_bind = "skill.bind"


_READ_ACTIONS = frozenset(
    {
        WorkspaceAction.asset_read,
        WorkspaceAction.models_read,
        WorkspaceAction.secrets_read,
        WorkspaceAction.skill_read,
    }
)

_RUNNER_ACTIONS = _READ_ACTIONS | frozenset({WorkspaceAction.asset_create, WorkspaceAction.asset_use})

_WORKSPACE_ROLE_ACTIONS: dict[str, frozenset[WorkspaceAction]] = {
    "viewer": _READ_ACTIONS,
    "runner": _RUNNER_ACTIONS,
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


@dataclass(frozen=True, slots=True)
class _WorkspaceAuthorizationContext:
    authorized: AuthorizedWorkspace
    bindings: tuple[RoleBindingRecord, ...]


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

    context = await _load_workspace_authorization(session, actor=actor, workspace_id=workspace_id)
    if action not in _workspace_permissions(context.bindings):
        raise AuthorizationError("permission_denied", concealed=True)
    return context.authorized


async def authorize_agent_skill_binding(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    agent_preset_id: str,
) -> AuthorizedWorkspace:
    """Authorize Skill binding through broad Workspace or direct AgentPreset Builder authority."""

    context = await _load_workspace_authorization(
        session,
        actor=actor,
        workspace_id=workspace_id,
        agent_preset_id=agent_preset_id,
    )
    permissions = _workspace_permissions(context.bindings)
    direct_builder = any(
        binding.resource_type == "agent_preset"
        and binding.resource_id == agent_preset_id
        and binding.role_key == "builder"
        for binding in context.bindings
    )
    if WorkspaceAction.skill_read not in permissions or (
        WorkspaceAction.skill_bind not in permissions and not direct_builder
    ):
        raise AuthorizationError("permission_denied", concealed=True)
    return context.authorized


async def _load_workspace_authorization(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    agent_preset_id: str | None = None,
) -> _WorkspaceAuthorizationContext:
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

    bindings = tuple(
        (await session.scalars(_binding_query(actor.principal, workspace, agent_preset_id=agent_preset_id))).all()
    )
    if actor.principal.principal_type is PrincipalType.user and not any(
        item.resource_type == "organization" for item in bindings
    ):
        raise AuthorizationError("organization_membership_required", concealed=True)

    return _WorkspaceAuthorizationContext(
        authorized=AuthorizedWorkspace(
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            actor=actor,
        ),
        bindings=bindings,
    )


def _workspace_permissions(bindings: tuple[RoleBindingRecord, ...]) -> frozenset[WorkspaceAction]:
    permissions: set[WorkspaceAction] = set()
    for binding in bindings:
        if binding.resource_type == "organization" and binding.role_key == "admin":
            permissions.update(frozenset(WorkspaceAction))
        elif binding.resource_type == "workspace":
            permissions.update(_WORKSPACE_ROLE_ACTIONS.get(binding.role_key, ()))
    return frozenset(permissions)


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


def _binding_query(
    principal: PrincipalRef,
    workspace: WorkspaceRecord,
    *,
    agent_preset_id: str | None,
) -> Select[tuple[RoleBindingRecord]]:
    resource_scope = or_(
        and_(
            RoleBindingRecord.resource_type == "organization",
            RoleBindingRecord.resource_id == workspace.organization_id,
        ),
        and_(
            RoleBindingRecord.resource_type == "workspace",
            RoleBindingRecord.resource_id == workspace.id,
            RoleBindingRecord.workspace_id == workspace.id,
        ),
    )
    if agent_preset_id is not None:
        resource_scope = or_(
            resource_scope,
            and_(
                RoleBindingRecord.resource_type == "agent_preset",
                RoleBindingRecord.resource_id == agent_preset_id,
                RoleBindingRecord.workspace_id == workspace.id,
            ),
        )
    return select(RoleBindingRecord).where(
        RoleBindingRecord.organization_id == workspace.organization_id,
        RoleBindingRecord.principal_type == principal.principal_type.value,
        RoleBindingRecord.principal_id == principal.principal_id,
        resource_scope,
    )
