"""Request-local IAM authorization for Workspace-owned resources."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.credentials import require_current_credential
from .domain import AuthenticatedActor, AuthorizationError, PrincipalRef, PrincipalType
from .models import OrganizationRecord, RoleBindingRecord, ServiceAccountRecord, UserRecord, WorkspaceRecord
from .role_rules import validate_binding


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
    search_provider_read = "search_provider.read"
    search_provider_manage = "search_provider.manage"
    models_read = "models.read"
    models_manage = "models.manage"
    secrets_read = "secrets.read"
    secrets_manage = "secrets.manage"
    skill_read = "skill.read"
    skill_create = "skill.create"
    skill_revision_publish = "skill.revision.publish"
    skill_update = "skill.update"
    skill_delete = "skill.delete"
    skill_bind = "skill.bind"
    environment_provider_read = "environment_provider.read"
    environment_provider_manage = "environment_provider.manage"
    environment_template_read = "environment_template.read"
    environment_template_manage = "environment_template.manage"
    environment_template_use = "environment_template.use"
    environment_read = "environment.read"
    environment_manage = "environment.manage"
    environment_use = "environment.use"
    secrets_bind = "secrets.bind"
    session_read = "session.read"
    session_labels_update = "session.labels.update"
    thread_read = "thread.read"
    thread_labels_update = "thread.labels.update"
    run_read = "run.read"
    run_labels_update = "run.labels.update"
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
    a2a_push_configuration_read = "a2a_push_configuration.read"
    a2a_push_configuration_manage = "a2a_push_configuration.manage"
    application_account_read = "application_account.read"
    application_account_manage = "application_account.manage"
    application_account_use = "application_account.use"
    account_target_read = "account_target.read"
    account_target_manage = "account_target.manage"
    connector_provider_read = "connector_provider.read"
    connector_provider_manage = "connector_provider.manage"
    connection_read = "connection.read"
    connection_manage = "connection.manage"
    invitation_manage = "invitation.manage"
    role_binding_manage = "role_binding.manage"
    service_account_manage = "service_account.manage"
    api_key_manage = "api_key.manage"
    security_audit_read = "security_audit.read"


_READ_ACTIONS = frozenset(
    {
        WorkspaceAction.asset_read,
        WorkspaceAction.agent_read,
        WorkspaceAction.models_read,
        WorkspaceAction.search_provider_read,
        WorkspaceAction.secrets_read,
        WorkspaceAction.skill_read,
        WorkspaceAction.environment_provider_read,
        WorkspaceAction.environment_template_read,
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
        WorkspaceAction.a2a_push_configuration_read,
        WorkspaceAction.application_account_read,
        WorkspaceAction.account_target_read,
        WorkspaceAction.connector_provider_read,
        WorkspaceAction.connection_read,
    }
)

_RUNNER_ACTIONS = _READ_ACTIONS | frozenset(
    {
        WorkspaceAction.application_account_use,
        WorkspaceAction.agent_invoke,
        WorkspaceAction.asset_create,
        WorkspaceAction.asset_use,
        WorkspaceAction.environment_use,
        WorkspaceAction.environment_template_use,
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
        WorkspaceAction.a2a_push_configuration_manage,
    }
)

_CONNECTIVITY_ADMIN_ACTIONS = frozenset(
    {
        WorkspaceAction.application_account_manage,
        WorkspaceAction.connector_provider_manage,
    }
)

_BUILDER_ACTIONS = _RUNNER_ACTIONS | frozenset(
    {
        WorkspaceAction.connection_manage,
        WorkspaceAction.agent_create,
        WorkspaceAction.agent_update,
        WorkspaceAction.agent_revision_create,
        WorkspaceAction.agent_current_revision_set,
        WorkspaceAction.agent_lifecycle,
        WorkspaceAction.agent_duplicate,
        WorkspaceAction.asset_delete,
        WorkspaceAction.models_manage,
        WorkspaceAction.search_provider_manage,
        WorkspaceAction.secrets_manage,
        WorkspaceAction.secrets_bind,
        WorkspaceAction.skill_create,
        WorkspaceAction.skill_revision_publish,
        WorkspaceAction.skill_update,
        WorkspaceAction.skill_delete,
        WorkspaceAction.skill_bind,
        WorkspaceAction.environment_provider_manage,
        WorkspaceAction.environment_template_manage,
        WorkspaceAction.environment_manage,
        WorkspaceAction.session_labels_update,
        WorkspaceAction.thread_labels_update,
        WorkspaceAction.run_labels_update,
        WorkspaceAction.hook_subscription_update,
        WorkspaceAction.hook_subscription_delete,
        WorkspaceAction.hook_subscription_redrive,
        WorkspaceAction.account_target_manage,
    }
)
_ADMIN_ACTIONS = (
    _BUILDER_ACTIONS
    | _CONNECTIVITY_ADMIN_ACTIONS
    | frozenset(
        {
            WorkspaceAction.invitation_manage,
            WorkspaceAction.role_binding_manage,
            WorkspaceAction.service_account_manage,
            WorkspaceAction.api_key_manage,
            WorkspaceAction.security_audit_read,
        }
    )
)

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
        WorkspaceAction.a2a_push_configuration_read,
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
        WorkspaceAction.a2a_push_configuration_manage,
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
class AuthorizedWorkspace:
    organization_id: str
    workspace_id: str
    actor: AuthenticatedActor


@dataclass(frozen=True, slots=True)
class AuthorizedAgentCollection:
    workspace: AuthorizedWorkspace
    visible_agent_ids: frozenset[str] | None


@dataclass(frozen=True, slots=True, repr=False)
class PrincipalPermissions:
    """Detached product actions and exact Agent scopes from one IAM read."""

    principal: PrincipalRef
    organization_id: str
    workspace_id: str
    workspace_actions: frozenset[WorkspaceAction]
    agent_actions: tuple[tuple[str, frozenset[WorkspaceAction]], ...]

    def for_agent(self, agent_id: str) -> frozenset[WorkspaceAction]:
        return self.workspace_actions | next(
            (actions for selected, actions in self.agent_actions if selected == agent_id), frozenset()
        )


async def read_principal_permissions(
    session: AsyncSession,
    *,
    principal: PrincipalRef,
    organization_id: str,
    workspace_id: str,
) -> PrincipalPermissions:
    """Evaluate current IAM once, including direct grants for this Workspace's Agents."""
    context = await _load_principal_authorization(
        session, principal=principal, workspace_id=workspace_id, include_agent_bindings=True
    )
    if context.workspace.organization_id != organization_id:
        raise AuthorizationError("workspace_not_found", concealed=True)
    return PrincipalPermissions(
        principal=principal,
        organization_id=organization_id,
        workspace_id=workspace_id,
        workspace_actions=_workspace_permissions(context.bindings),
        agent_actions=tuple(
            (agent_id, _agent_permissions(context.bindings, agent_id=agent_id))
            for agent_id in sorted(
                {binding.resource_id for binding in context.bindings if binding.resource_type == "agent"}
            )
        ),
    )


def _require_snapshot_scope(
    snapshot: PrincipalPermissions,
    *,
    principal: PrincipalRef,
    workspace_id: str,
    organization_id: str | None = None,
) -> None:
    if (
        snapshot.principal != principal
        or snapshot.workspace_id != workspace_id
        or (organization_id is not None and snapshot.organization_id != organization_id)
    ):
        raise AuthorizationError("permission_snapshot_scope_mismatch", concealed=True)
    # Workspace existence and ownership were checked by the snapshot refresh.


async def _authorize_actor_snapshot(
    session: AsyncSession, *, actor: AuthenticatedActor, workspace_id: str, snapshot: PrincipalPermissions
) -> AuthorizedWorkspace:
    # Only an explicitly constructed execution actor may use an Attempt snapshot.
    # Browser/API credentials and their stream continuations always read current IAM.
    if actor.auth_method != "internal" or actor.credential_source != "host" or actor.workspace_id != workspace_id:
        raise AuthorizationError("permission_snapshot_scope_mismatch", concealed=True)
    _require_snapshot_scope(snapshot, principal=actor.principal, workspace_id=workspace_id)
    return AuthorizedWorkspace(organization_id=snapshot.organization_id, workspace_id=workspace_id, actor=actor)


@dataclass(frozen=True, slots=True)
class _WorkspaceAuthorizationContext:
    authorized: AuthorizedWorkspace
    bindings: tuple[RoleBindingRecord, ...]


@dataclass(frozen=True, slots=True)
class _PrincipalAuthorizationContext:
    workspace: WorkspaceRecord
    bindings: tuple[RoleBindingRecord, ...]


async def authorize_workspace(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    action: WorkspaceAction,
    snapshot: PrincipalPermissions | None = None,
) -> AuthorizedWorkspace:
    """Authorize one operation from current Principal, credential boundary, and grants."""

    if snapshot is not None:
        authorized = await _authorize_actor_snapshot(session, actor=actor, workspace_id=workspace_id, snapshot=snapshot)
        if action not in snapshot.workspace_actions:
            raise AuthorizationError("permission_denied", concealed=True)
        return authorized
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
    snapshot: PrincipalPermissions | None = None,
) -> AuthorizedWorkspace:
    """Authorize one stable Agent through Workspace or direct Agent roles."""

    if snapshot is not None:
        authorized = await _authorize_actor_snapshot(session, actor=actor, workspace_id=workspace_id, snapshot=snapshot)
        if action not in snapshot.for_agent(agent_id):
            raise AuthorizationError("permission_denied", concealed=True)
        return authorized
    context = await _load_workspace_authorization(
        session,
        actor=actor,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )
    if action not in _agent_permissions(context.bindings, agent_id=agent_id):
        raise AuthorizationError("permission_denied", concealed=True)
    return context.authorized


async def authorize_persisted_workspace_principal_action(
    session: AsyncSession,
    *,
    principal: PrincipalRef,
    organization_id: str,
    workspace_id: str,
    action: WorkspaceAction,
    snapshot: PrincipalPermissions | None = None,
) -> None:
    """Authorize deferred Workspace commands from their original Principal."""
    if snapshot is not None:
        _require_snapshot_scope(
            snapshot, principal=principal, workspace_id=workspace_id, organization_id=organization_id
        )
        if action not in snapshot.workspace_actions:
            raise AuthorizationError("permission_denied", concealed=True)
        return
    context = await _load_principal_authorization(session, principal=principal, workspace_id=workspace_id)
    if context.workspace.organization_id != organization_id:
        raise AuthorizationError("workspace_not_found", concealed=True)
    if action not in _workspace_permissions(context.bindings):
        raise AuthorizationError("permission_denied", concealed=True)


async def authorize_persisted_agent_principal_actions(
    session: AsyncSession,
    *,
    principal: PrincipalRef,
    organization_id: str,
    workspace_id: str,
    agent_id: str,
    actions: frozenset[WorkspaceAction],
    snapshot: PrincipalPermissions | None = None,
) -> None:
    """Reauthorize durable Principal actions without inventing a request credential."""

    if not actions:
        raise ValueError("persisted Principal authorization requires at least one action")

    if snapshot is not None:
        _require_snapshot_scope(
            snapshot, principal=principal, workspace_id=workspace_id, organization_id=organization_id
        )
        if not actions.issubset(snapshot.for_agent(agent_id)):
            raise AuthorizationError("permission_denied", concealed=True)
        return
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
    await require_current_credential(session, actor)
    if actor.boundary_workspace_id is not None and actor.boundary_workspace_id != workspace_id:
        raise AuthorizationError("credential_boundary_mismatch", concealed=True)

    principal_context = await _load_principal_authorization(
        session,
        principal=actor.principal,
        workspace_id=workspace_id,
        agent_id=agent_id,
        include_agent_bindings=include_agent_bindings,
    )
    if (
        actor.boundary_organization_id is not None
        and actor.boundary_organization_id != principal_context.workspace.organization_id
    ):
        raise AuthorizationError("credential_boundary_mismatch", concealed=True)
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
    for binding in bindings:
        validate_binding(binding)
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


async def authorize_organization_admin(session: AsyncSession, *, actor: AuthenticatedActor) -> str:
    await require_current_credential(session, actor)
    organization_id = actor.boundary_organization_id
    if organization_id is None:
        raise AuthorizationError("permission_denied", concealed=True)
    await authorize_organization_admin_principal(session, principal=actor.principal, organization_id=organization_id)
    return organization_id


async def authorize_organization_admin_principal(
    session: AsyncSession, *, principal: PrincipalRef, organization_id: str
) -> None:
    """Check current Organization authority for a durable User, without inventing a credential."""
    if await organization_role(session, principal=principal, organization_id=organization_id) != "admin":
        raise AuthorizationError("permission_denied", concealed=True)


async def organization_role(session: AsyncSession, *, principal: PrincipalRef, organization_id: str) -> str:
    """Read current Organization membership for an active User."""
    if principal.principal_type is not PrincipalType.user:
        raise AuthorizationError("permission_denied", concealed=True)
    await _require_active_user(session, principal.principal_id)
    organization = await session.get(OrganizationRecord, organization_id)
    role = await session.scalar(
        select(RoleBindingRecord.role_key).where(
            RoleBindingRecord.organization_id == organization_id,
            RoleBindingRecord.resource_type == "organization",
            RoleBindingRecord.resource_id == organization_id,
            RoleBindingRecord.workspace_id.is_(None),
            RoleBindingRecord.principal_type == "user",
            RoleBindingRecord.principal_id == principal.principal_id,
        )
    )
    if organization is None or role is None:
        raise AuthorizationError("permission_denied", concealed=True)
    return role


async def workspace_permissions(
    session: AsyncSession, *, actor: AuthenticatedActor, workspace_id: str
) -> tuple[frozenset[WorkspaceAction], bool]:
    """A current UI hint, never a reusable authorization grant."""
    context = await _load_workspace_authorization(session, actor=actor, workspace_id=workspace_id)
    actions = _workspace_permissions(context.bindings)
    if not actions:
        raise AuthorizationError("permission_denied", concealed=True)
    organization_admin = any(
        binding.resource_type == "organization" and binding.role_key == "admin" for binding in context.bindings
    )
    return actions, organization_admin
