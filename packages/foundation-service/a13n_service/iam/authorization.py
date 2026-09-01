"""Request-local IAM authorization for Workspace-owned resources."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import PrincipalRef, PrincipalType
from .models import RoleBindingRecord, ServiceAccountRecord, UserRecord, WorkspaceRecord


class WorkspaceAction(StrEnum):
    agent_preset_read = "agent_preset.read"
    agent_preset_create = "agent_preset.create"
    agent_preset_update = "agent_preset.update"
    agent_preset_publish = "agent_preset.publish"
    agent_preset_lifecycle = "agent_preset.lifecycle"
    agent_preset_duplicate = "agent_preset.duplicate"
    agent_preset_invoke = "agent_preset.invoke"
    asset_read = "asset.read"
    asset_create = "asset.create"
    asset_use = "asset.use"
    asset_delete = "asset.delete"
    models_read = "models.read"
    models_manage = "models.manage"
    plugin_read = "plugin.read"
    plugin_manage = "plugin.manage"
    plugin_runtime_manage = "plugin.runtime.manage"
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
    skill_read = "skill.read"
    skill_create = "skill.create"
    skill_update = "skill.update"
    skill_delete = "skill.delete"
    skill_bind = "skill.bind"


_READ_ACTIONS = frozenset(
    {
        WorkspaceAction.asset_read,
        WorkspaceAction.agent_preset_read,
        WorkspaceAction.models_read,
        WorkspaceAction.plugin_read,
        WorkspaceAction.secrets_read,
        WorkspaceAction.connector_read,
        WorkspaceAction.connection_read,
        WorkspaceAction.trigger_read,
        WorkspaceAction.skill_read,
    }
)

_RUNNER_ACTIONS = _READ_ACTIONS | frozenset(
    {
        WorkspaceAction.agent_preset_invoke,
        WorkspaceAction.asset_create,
        WorkspaceAction.asset_use,
        WorkspaceAction.connector_invoke,
    }
)

_WORKSPACE_ROLE_ACTIONS: dict[str, frozenset[WorkspaceAction]] = {
    "viewer": _READ_ACTIONS,
    "runner": _RUNNER_ACTIONS,
    "builder": frozenset(WorkspaceAction) - {WorkspaceAction.plugin_manage, WorkspaceAction.plugin_runtime_manage},
    "admin": frozenset(WorkspaceAction) - {WorkspaceAction.plugin_manage, WorkspaceAction.plugin_runtime_manage},
}

_DIRECT_AGENT_ROLE_ACTIONS: dict[str, frozenset[WorkspaceAction]] = {
    "viewer": frozenset({WorkspaceAction.agent_preset_read}),
    "runner": frozenset({WorkspaceAction.agent_preset_read, WorkspaceAction.agent_preset_invoke}),
    "builder": frozenset(
        {
            WorkspaceAction.agent_preset_read,
            WorkspaceAction.agent_preset_invoke,
            WorkspaceAction.agent_preset_update,
            WorkspaceAction.agent_preset_publish,
            WorkspaceAction.agent_preset_lifecycle,
            WorkspaceAction.skill_bind,
        }
    ),
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
class AuthorizedAgentPresetCollection:
    workspace: AuthorizedWorkspace
    visible_preset_ids: frozenset[str] | None


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


async def authorize_agent_preset(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    agent_preset_id: str,
    action: WorkspaceAction,
) -> AuthorizedWorkspace:
    """Authorize one stable AgentPreset through Workspace or direct Preset roles."""

    context = await _load_workspace_authorization(
        session,
        actor=actor,
        workspace_id=workspace_id,
        agent_preset_id=agent_preset_id,
    )
    permissions = set(_workspace_permissions(context.bindings))
    for binding in context.bindings:
        if binding.resource_type == "agent_preset" and binding.resource_id == agent_preset_id:
            permissions.update(_DIRECT_AGENT_ROLE_ACTIONS.get(binding.role_key, ()))
    if action not in permissions:
        raise AuthorizationError("permission_denied", concealed=True)
    return context.authorized


async def authorize_agent_preset_collection(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
) -> AuthorizedAgentPresetCollection:
    """Authorize a Preset collection and project direct-only visibility."""

    context = await _load_workspace_authorization(
        session,
        actor=actor,
        workspace_id=workspace_id,
        include_agent_preset_bindings=True,
    )
    if WorkspaceAction.agent_preset_read in _workspace_permissions(context.bindings):
        return AuthorizedAgentPresetCollection(workspace=context.authorized, visible_preset_ids=None)
    visible = frozenset(
        binding.resource_id
        for binding in context.bindings
        if binding.resource_type == "agent_preset"
        and WorkspaceAction.agent_preset_read in _DIRECT_AGENT_ROLE_ACTIONS.get(binding.role_key, ())
    )
    if not visible:
        raise AuthorizationError("permission_denied", concealed=True)
    return AuthorizedAgentPresetCollection(workspace=context.authorized, visible_preset_ids=visible)


async def _load_workspace_authorization(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    agent_preset_id: str | None = None,
    include_agent_preset_bindings: bool = False,
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
        (
            await session.scalars(
                _binding_query(
                    actor.principal,
                    workspace,
                    agent_preset_id=agent_preset_id,
                    include_agent_preset_bindings=include_agent_preset_bindings,
                )
            )
        ).all()
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
    include_agent_preset_bindings: bool = False,
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
    elif include_agent_preset_bindings:
        resource_scope = or_(
            resource_scope,
            and_(
                RoleBindingRecord.resource_type == "agent_preset",
                RoleBindingRecord.workspace_id == workspace.id,
            ),
        )
    return select(RoleBindingRecord).where(
        RoleBindingRecord.organization_id == workspace.organization_id,
        RoleBindingRecord.principal_type == principal.principal_type.value,
        RoleBindingRecord.principal_id == principal.principal_id,
        resource_scope,
    )
