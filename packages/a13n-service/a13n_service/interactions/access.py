"""Shared interaction access, including owner-scoped configuration conversations."""

from __future__ import annotations

from sqlalchemy import and_, false, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_agent,
    authorize_workspace,
)
from a13n_service.iam.authorization import AuthorizedWorkspace, read_principal_permissions
from a13n_service.iam.domain import PrincipalType

from .domain import Run
from .models import SessionRecord


async def configuration_visibility(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    action: WorkspaceAction,
):
    """A SQL predicate for configuration Sessions, applied before pagination."""
    from a13n_service.agent_configuration.models import ConfigurationDraftRecord
    from a13n_service.agents.models import AgentRecord

    if actor.principal.principal_type is not PrincipalType.user:
        return false()
    permissions = await read_principal_permissions(
        session, principal=actor.principal, organization_id=organization_id, workspace_id=workspace_id
    )
    required = {WorkspaceAction.agent_read, action}
    target_ids = [key for key, _ in permissions.agent_actions if required.issubset(permissions.for_agent(key))]
    target_permission = true() if required.issubset(permissions.workspace_actions) else AgentRecord.id.in_(target_ids)
    target = (
        select(AgentRecord.id)
        .where(
            AgentRecord.id == ConfigurationDraftRecord.target_agent_id,
            AgentRecord.organization_id == organization_id,
            AgentRecord.workspace_id == workspace_id,
            AgentRecord.system_purpose.is_(None),
            target_permission,
        )
        .exists()
    )
    creation = {WorkspaceAction.agent_create, action}.issubset(permissions.workspace_actions)
    draft_scope = (
        select(ConfigurationDraftRecord.id)
        .where(
            ConfigurationDraftRecord.id == SessionRecord.configuration_draft_id,
            ConfigurationDraftRecord.session_id == SessionRecord.id,
            ConfigurationDraftRecord.workspace_id == workspace_id,
            ConfigurationDraftRecord.organization_id == organization_id,
            or_(target, and_(ConfigurationDraftRecord.target_agent_id.is_(None), true() if creation else false())),
        )
        .correlate(SessionRecord)
        .exists()
    )
    return and_(SessionRecord.configuration_owner_user_id == actor.principal.principal_id, draft_scope)


_READ_ACTIONS = frozenset(
    {
        WorkspaceAction.session_read,
        WorkspaceAction.thread_read,
        WorkspaceAction.run_read,
        WorkspaceAction.trace_read,
        WorkspaceAction.notification_subscribe,
        WorkspaceAction.lifecycle_event_read,
    }
)


async def authorize_interaction(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    session_id: str,
    agent_id: str | None,
    action: WorkspaceAction,
) -> AuthorizedWorkspace:
    conversation = await session.get(SessionRecord, session_id)
    if conversation is None or conversation.workspace_id != workspace_id:
        raise AuthorizationError("run_not_found", concealed=True)
    if conversation.configuration_owner_user_id is not None:
        from a13n_service.agent_configuration.authorization import authorize_session
        from a13n_service.agents.models import AgentRecord

        if agent_id is not None:
            agent = await session.get(AgentRecord, agent_id)
            if agent is None or agent.workspace_id != workspace_id or agent.system_purpose != "configuration_assistant":
                raise AuthorizationError("run_not_found", concealed=True)
        await authorize_session(
            session, actor=actor, session_id=session_id, write=action not in _READ_ACTIONS, action=action
        )
        return AuthorizedWorkspace(conversation.organization_id, workspace_id, actor)
    if agent_id is None:
        return await authorize_workspace(session, actor=actor, workspace_id=workspace_id, action=action)
    return await authorize_agent(session, actor=actor, workspace_id=workspace_id, agent_id=agent_id, action=action)


async def authorize_retained_execution(session: AsyncSession, *, source: Run, workspace_id: str) -> None:
    if source.configuration_context is not None:
        from a13n_service.agent_configuration.authorization import authorize_execution

        await authorize_execution(
            session,
            principal=source.authority_principal,
            organization_id=source.organization_id,
            workspace_id=workspace_id,
            agent_id=source.agent_id,
            context=source.configuration_context,
        )
        return
    from a13n_service.iam.authorization import authorize_persisted_agent_principal_actions

    await authorize_persisted_agent_principal_actions(
        session,
        principal=source.authority_principal,
        organization_id=source.organization_id,
        workspace_id=workspace_id,
        agent_id=source.agent_id,
        actions=frozenset({WorkspaceAction.agent_invoke}),
    )
