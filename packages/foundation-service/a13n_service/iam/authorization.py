"""Request-local IAM authorization for Workspace-owned resources."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import PrincipalRef, PrincipalType
from .models import RoleBindingRecord, ServiceAccountRecord, UserRecord, WorkspaceRecord


class WorkspaceAction(StrEnum):
    agent_read = "agent.read"
    agent_create = "agent.create"
    agent_update = "agent.update"
    agent_revision_create = "agent.revision.create"
    agent_current_revision_set = "agent.current_revision.set"
    agent_lifecycle = "agent.lifecycle"
    agent_duplicate = "agent.duplicate"
    agent_invoke = "agent.invoke"
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
    skill_read = "skill.read"
    skill_create = "skill.create"
    skill_revision_publish = "skill.revision.publish"
    skill_update = "skill.update"
    skill_delete = "skill.delete"
    skill_bind = "skill.bind"
    environment_provider_read = "environment_provider.read"
    environment_provider_select = "environment_provider.select"
    environment_read = "environment.read"
    environment_manage = "environment.manage"
    environment_test = "environment.test"
    environment_use = "environment.use"
    secrets_bind = "secrets.bind"
    session_read = "session.read"
    thread_read = "thread.read"
    run_read = "run.read"
    lifecycle_event_read = "lifecycle_event.read"
    notification_subscribe = "notification.subscribe"
    usage_read = "usage.read"
    trace_read = "trace.read"
    hook_subscription_read = "hook_subscription.read"
    hook_subscription_create = "hook_subscription.create"
    hook_subscription_update = "hook_subscription.update"
    hook_subscription_delete = "hook_subscription.delete"
    hook_subscription_redrive = "hook_subscription.redrive"
    run_continue = "run.continue"
    run_fork = "run.fork"
    run_retry = "run.retry"
    run_feedback = "run.feedback"
    run_steer = "run.steer"
    run_interrupt = "run.interrupt"
    queued_submission_read = "queued_submission.read"
    queued_submission_create = "queued_submission.create"
    queued_submission_update = "queued_submission.update"
    queued_submission_delete = "queued_submission.delete"
    queued_submission_reorder = "queued_submission.reorder"
    queued_submission_consume = "queued_submission.consume"
    application_account_read = "application_account.read"
    application_account_manage = "application_account.manage"
    application_account_use = "application_account.use"
    ingress_read = "ingress.read"
    ingress_manage = "ingress.manage"
    route_read = "route.read"
    route_manage = "route.manage"
    connector_provider_read = "connector_provider.read"
    connector_provider_manage = "connector_provider.manage"
    connector_connection_read = "connector_connection.read"
    connector_connection_manage = "connector_connection.manage"
    mcp_connection_read = "mcp_connection.read"
    mcp_connection_manage = "mcp_connection.manage"


_READ_ACTIONS = frozenset(
    {
        WorkspaceAction.asset_read,
        WorkspaceAction.agent_read,
        WorkspaceAction.models_read,
        WorkspaceAction.plugin_read,
        WorkspaceAction.secrets_read,
        WorkspaceAction.skill_read,
        WorkspaceAction.environment_provider_read,
        WorkspaceAction.environment_read,
        WorkspaceAction.session_read,
        WorkspaceAction.thread_read,
        WorkspaceAction.run_read,
        WorkspaceAction.lifecycle_event_read,
        WorkspaceAction.notification_subscribe,
        WorkspaceAction.usage_read,
        WorkspaceAction.trace_read,
        WorkspaceAction.hook_subscription_read,
        WorkspaceAction.queued_submission_read,
        WorkspaceAction.application_account_read,
        WorkspaceAction.ingress_read,
        WorkspaceAction.route_read,
        WorkspaceAction.connector_provider_read,
        WorkspaceAction.connector_connection_read,
        WorkspaceAction.mcp_connection_read,
    }
)

_RUNNER_ACTIONS = _READ_ACTIONS | frozenset(
    {
        WorkspaceAction.application_account_use,
        WorkspaceAction.agent_invoke,
        WorkspaceAction.asset_create,
        WorkspaceAction.asset_use,
        WorkspaceAction.environment_use,
        WorkspaceAction.hook_subscription_create,
        WorkspaceAction.run_continue,
        WorkspaceAction.run_fork,
        WorkspaceAction.run_retry,
        WorkspaceAction.run_feedback,
        WorkspaceAction.run_steer,
        WorkspaceAction.run_interrupt,
        WorkspaceAction.queued_submission_create,
        WorkspaceAction.queued_submission_update,
        WorkspaceAction.queued_submission_delete,
        WorkspaceAction.queued_submission_reorder,
        WorkspaceAction.queued_submission_consume,
    }
)

_PLUGIN_OPERATOR_ACTIONS = frozenset(
    {
        WorkspaceAction.plugin_manage,
        WorkspaceAction.plugin_runtime_manage,
    }
)

_CONNECTIVITY_ADMIN_ACTIONS = frozenset(
    {
        WorkspaceAction.application_account_manage,
        WorkspaceAction.ingress_manage,
        WorkspaceAction.connector_provider_manage,
        WorkspaceAction.connector_connection_manage,
        WorkspaceAction.mcp_connection_manage,
    }
)

_BUILDER_ACTIONS = frozenset(WorkspaceAction) - _PLUGIN_OPERATOR_ACTIONS - _CONNECTIVITY_ADMIN_ACTIONS
_ADMIN_ACTIONS = frozenset(WorkspaceAction) - _PLUGIN_OPERATOR_ACTIONS

_WORKSPACE_ROLE_ACTIONS: dict[str, frozenset[WorkspaceAction]] = {
    "viewer": _READ_ACTIONS,
    "runner": _RUNNER_ACTIONS,
    "builder": _BUILDER_ACTIONS,
    "admin": _ADMIN_ACTIONS,
}

_DIRECT_AGENT_VIEWER_ACTIONS = frozenset(
    {
        WorkspaceAction.agent_read,
        WorkspaceAction.session_read,
        WorkspaceAction.thread_read,
        WorkspaceAction.run_read,
        WorkspaceAction.lifecycle_event_read,
        WorkspaceAction.notification_subscribe,
        WorkspaceAction.trace_read,
    }
)

_DIRECT_AGENT_RUNNER_ACTIONS = _DIRECT_AGENT_VIEWER_ACTIONS | frozenset(
    {
        WorkspaceAction.agent_invoke,
        WorkspaceAction.run_continue,
        WorkspaceAction.run_fork,
        WorkspaceAction.run_retry,
        WorkspaceAction.run_feedback,
        WorkspaceAction.run_steer,
        WorkspaceAction.run_interrupt,
        WorkspaceAction.queued_submission_read,
        WorkspaceAction.queued_submission_create,
        WorkspaceAction.queued_submission_update,
        WorkspaceAction.queued_submission_delete,
        WorkspaceAction.queued_submission_reorder,
        WorkspaceAction.queued_submission_consume,
    }
)

_DIRECT_AGENT_ROLE_ACTIONS: dict[str, frozenset[WorkspaceAction]] = {
    "viewer": _DIRECT_AGENT_VIEWER_ACTIONS,
    "runner": _DIRECT_AGENT_RUNNER_ACTIONS,
    "builder": frozenset(
        {
            *_DIRECT_AGENT_RUNNER_ACTIONS,
            WorkspaceAction.agent_update,
            WorkspaceAction.agent_revision_create,
            WorkspaceAction.agent_current_revision_set,
            WorkspaceAction.agent_lifecycle,
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
class AuthorizedAgentCollection:
    workspace: AuthorizedWorkspace
    visible_agent_ids: frozenset[str] | None


@dataclass(frozen=True, slots=True)
class _WorkspaceAuthorizationContext:
    authorized: AuthorizedWorkspace
    bindings: tuple[RoleBindingRecord, ...]


@dataclass(frozen=True, slots=True)
class _PrincipalAuthorizationContext:
    workspace: WorkspaceRecord
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
    agent_id: str,
) -> AuthorizedWorkspace:
    """Authorize Skill binding through broad Workspace or direct Agent Builder authority."""

    context = await _load_workspace_authorization(
        session,
        actor=actor,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )
    permissions = _workspace_permissions(context.bindings)
    direct_builder = any(
        binding.resource_type == "agent" and binding.resource_id == agent_id and binding.role_key == "builder"
        for binding in context.bindings
    )
    if WorkspaceAction.skill_read not in permissions or (
        WorkspaceAction.skill_bind not in permissions and not direct_builder
    ):
        raise AuthorizationError("permission_denied", concealed=True)
    return context.authorized


async def authorize_agent(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    agent_id: str,
    action: WorkspaceAction,
) -> AuthorizedWorkspace:
    """Authorize one stable Agent through Workspace or direct Agent roles."""

    context = await _load_workspace_authorization(
        session,
        actor=actor,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )
    if action not in _agent_permissions(context.bindings, agent_id=agent_id):
        raise AuthorizationError("permission_denied", concealed=True)
    return context.authorized


async def authorize_persisted_agent_principal_actions(
    session: AsyncSession,
    *,
    principal: PrincipalRef,
    organization_id: str,
    workspace_id: str,
    agent_id: str,
    actions: frozenset[WorkspaceAction],
) -> None:
    """Reauthorize durable Principal actions without inventing a request credential."""

    if not actions:
        raise ValueError("persisted Principal authorization requires at least one action")

    context = await _load_principal_authorization(
        session,
        principal=principal,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )
    if context.workspace.organization_id != organization_id:
        raise AuthorizationError("workspace_not_found", concealed=True)
    if not actions.issubset(_agent_permissions(context.bindings, agent_id=agent_id)):
        raise AuthorizationError("permission_denied", concealed=True)


async def authorize_agent_collection(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
) -> AuthorizedAgentCollection:
    """Authorize an Agent collection and project direct-only visibility."""

    return await authorize_agent_scoped_collection(
        session,
        actor=actor,
        workspace_id=workspace_id,
        action=WorkspaceAction.agent_read,
    )


async def authorize_agent_scoped_collection(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    action: WorkspaceAction,
) -> AuthorizedAgentCollection:
    """Authorize a Workspace collection whose rows are owned through Agents."""

    context = await _load_workspace_authorization(
        session,
        actor=actor,
        workspace_id=workspace_id,
        include_agent_bindings=True,
    )
    if action in _workspace_permissions(context.bindings):
        return AuthorizedAgentCollection(workspace=context.authorized, visible_agent_ids=None)
    visible = frozenset(
        binding.resource_id
        for binding in context.bindings
        if binding.resource_type == "agent" and action in _DIRECT_AGENT_ROLE_ACTIONS.get(binding.role_key, ())
    )
    if not visible:
        raise AuthorizationError("permission_denied", concealed=True)
    return AuthorizedAgentCollection(workspace=context.authorized, visible_agent_ids=visible)


async def _load_workspace_authorization(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    agent_id: str | None = None,
    include_agent_bindings: bool = False,
) -> _WorkspaceAuthorizationContext:
    if actor.boundary_workspace_id != workspace_id:
        raise AuthorizationError("credential_boundary_mismatch", concealed=True)

    principal_context = await _load_principal_authorization(
        session,
        principal=actor.principal,
        workspace_id=workspace_id,
        agent_id=agent_id,
        include_agent_bindings=include_agent_bindings,
    )
    return _WorkspaceAuthorizationContext(
        authorized=AuthorizedWorkspace(
            organization_id=principal_context.workspace.organization_id,
            workspace_id=principal_context.workspace.id,
            actor=actor,
        ),
        bindings=principal_context.bindings,
    )


async def _load_principal_authorization(
    session: AsyncSession,
    *,
    principal: PrincipalRef,
    workspace_id: str,
    agent_id: str | None = None,
    include_agent_bindings: bool = False,
) -> _PrincipalAuthorizationContext:

    workspace = await session.scalar(
        select(WorkspaceRecord).where(WorkspaceRecord.id == workspace_id, WorkspaceRecord.deleted_at.is_(None))
    )
    if workspace is None:
        raise AuthorizationError("workspace_not_found", concealed=True)

    if principal.principal_type is PrincipalType.user:
        await _require_active_user(session, principal.principal_id)
    else:
        await _require_active_service_account(session, principal.principal_id, workspace)

    bindings = tuple(
        (
            await session.scalars(
                _binding_query(
                    principal,
                    workspace,
                    agent_id=agent_id,
                    include_agent_bindings=include_agent_bindings,
                )
            )
        ).all()
    )
    if principal.principal_type is PrincipalType.user and not any(
        item.resource_type == "organization" for item in bindings
    ):
        raise AuthorizationError("organization_membership_required", concealed=True)

    return _PrincipalAuthorizationContext(
        workspace=workspace,
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


def _agent_permissions(
    bindings: tuple[RoleBindingRecord, ...],
    *,
    agent_id: str,
) -> frozenset[WorkspaceAction]:
    permissions = set(_workspace_permissions(bindings))
    for binding in bindings:
        if binding.resource_type == "agent" and binding.resource_id == agent_id:
            permissions.update(_DIRECT_AGENT_ROLE_ACTIONS.get(binding.role_key, ()))
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
    agent_id: str | None,
    include_agent_bindings: bool = False,
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
    if agent_id is not None:
        resource_scope = or_(
            resource_scope,
            and_(
                RoleBindingRecord.resource_type == "agent",
                RoleBindingRecord.resource_id == agent_id,
                RoleBindingRecord.workspace_id == workspace.id,
            ),
        )
    elif include_agent_bindings:
        resource_scope = or_(
            resource_scope,
            and_(
                RoleBindingRecord.resource_type == "agent",
                RoleBindingRecord.workspace_id == workspace.id,
            ),
        )
    return select(RoleBindingRecord).where(
        RoleBindingRecord.organization_id == workspace.organization_id,
        RoleBindingRecord.principal_type == principal.principal_type.value,
        RoleBindingRecord.principal_id == principal.principal_id,
        resource_scope,
    )
