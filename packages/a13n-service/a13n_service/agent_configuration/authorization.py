"""Configuration ownership and purpose predicates, independent of role names."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.domain import AgentConfig
from a13n_service.agents.models import AgentRecord
from a13n_service.iam import AuthenticatedActor, AuthorizationError, authorize_agent, authorize_workspace
from a13n_service.iam.authorization import (
    AuthorizedWorkspace,
    PrincipalPermissions,
    WorkspaceAction,
    authorize_persisted_agent_principal_actions,
    authorize_persisted_workspace_principal_action,
)
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.interactions.models import SessionRecord, ThreadRecord

from .context import ConfigurationRunContext
from .models import ConfigurationDraftRecord


async def authorize_candidate_snapshot(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    config: AgentConfig,
    target_agent_id: str | None,
    snapshot: PrincipalPermissions,
) -> None:
    """A model edit cannot gain resource authority between Attempt refreshes."""
    actions = {WorkspaceAction.models_read}
    if config.memory is not None:
        actions.add(WorkspaceAction.memory_provider_read)
    if config.connection_tools:
        actions.add(WorkspaceAction.connection_read)
    if config.secret_requirements:
        actions.add(WorkspaceAction.secrets_bind)
    web = config.toolsets.get("web")
    if web is not None and web.enabled:
        actions.add(WorkspaceAction.web_provider_read)
    if config.skills:
        if target_agent_id is None:
            actions.add(WorkspaceAction.skill_bind)
        else:
            await authorize_agent(
                session,
                actor=actor,
                workspace_id=actor.workspace_id,
                agent_id=target_agent_id,
                action=WorkspaceAction.skill_bind,
                snapshot=snapshot,
            )
    for selection in config.subagents.values():
        await authorize_agent(
            session,
            actor=actor,
            workspace_id=actor.workspace_id,
            agent_id=selection.agent_id,
            action=WorkspaceAction.agent_read,
            snapshot=snapshot,
        )
        if selection.environment.template_revision_id is not None:
            actions.add(WorkspaceAction.environment_template_use)
    for action in actions:
        await authorize_workspace(
            session, actor=actor, workspace_id=actor.workspace_id, action=action, snapshot=snapshot
        )


async def authorize_target(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    target_agent_id: str | None,
    write: bool = True,
    action: WorkspaceAction | None = None,
) -> None:
    """A current User may author only the selected business target or create scope."""
    if actor.principal.principal_type is not PrincipalType.user:
        raise AuthorizationError("configuration_not_found", concealed=True)
    if target_agent_id is None:
        await authorize_workspace(
            session,
            actor=actor,
            workspace_id=actor.workspace_id,
            action=WorkspaceAction.agent_create,
        )
        if action is not None:
            await authorize_workspace(session, actor=actor, workspace_id=actor.workspace_id, action=action)
        return
    target = await session.scalar(
        select(AgentRecord).where(AgentRecord.id == target_agent_id, AgentRecord.workspace_id == actor.workspace_id)
    )
    if target is None or target.system_purpose is not None:
        raise AuthorizationError("configuration_not_found", concealed=True)
    await authorize_agent(
        session,
        actor=actor,
        workspace_id=actor.workspace_id,
        agent_id=target.id,
        action=WorkspaceAction.agent_read,
    )
    if write:
        await authorize_agent(
            session,
            actor=actor,
            workspace_id=actor.workspace_id,
            agent_id=target.id,
            action=WorkspaceAction.agent_revision_create,
        )
    if action is not None:
        await authorize_agent(session, actor=actor, workspace_id=actor.workspace_id, agent_id=target.id, action=action)


async def authorize_session(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    session_id: str,
    write: bool = True,
    action: WorkspaceAction | None = None,
    lock: bool = False,
) -> SessionRecord:
    query = select(SessionRecord).where(
        SessionRecord.id == session_id,
        SessionRecord.workspace_id == actor.workspace_id,
        SessionRecord.configuration_owner_user_id == actor.principal.principal_id,
    )
    record = await session.scalar(query.with_for_update() if lock else query)
    if record is None or actor.principal.principal_type is not PrincipalType.user:
        raise AuthorizationError("configuration_not_found", concealed=True)
    draft = await session.get(ConfigurationDraftRecord, record.configuration_draft_id)
    if draft is None or draft.session_id != record.id or draft.workspace_id != record.workspace_id:
        raise AuthorizationError("configuration_not_found", concealed=True)
    await authorize_target(session, actor=actor, target_agent_id=draft.target_agent_id, write=write, action=action)
    return record


async def authorize_execution(
    session: AsyncSession,
    *,
    principal: PrincipalRef,
    organization_id: str,
    workspace_id: str,
    agent_id: str,
    context: ConfigurationRunContext,
    snapshot: PrincipalPermissions | None = None,
) -> None:
    """Validate one protected binding; never add agent.invoke to the IAM snapshot."""
    conversation = await session.get(SessionRecord, context.session_id)
    thread = await session.get(ThreadRecord, context.thread_id)
    draft = await session.get(ConfigurationDraftRecord, context.draft_id)
    agent = await session.get(AgentRecord, agent_id)
    if (
        principal.principal_type is not PrincipalType.user
        or conversation is None
        or thread is None
        or draft is None
        or agent is None
        or conversation.organization_id != organization_id
        or conversation.workspace_id != workspace_id
        or conversation.configuration_owner_user_id != principal.principal_id
        or thread.session_id != conversation.id
        or thread.organization_id != organization_id
        or conversation.configuration_draft_id != draft.id
        or draft.session_id != conversation.id
        or draft.workspace_id != workspace_id
        or draft.organization_id != organization_id
        or agent.workspace_id != workspace_id
        or agent.organization_id != organization_id
        or agent.system_purpose != "configuration_assistant"
        or agent.source != "builtin"
        or not agent.enabled
        or agent.archived_at is not None
    ):
        raise AuthorizationError("configuration_not_found", concealed=True)
    if draft.target_agent_id is None:
        await authorize_persisted_workspace_principal_action(
            session,
            principal=principal,
            organization_id=organization_id,
            workspace_id=workspace_id,
            action=WorkspaceAction.agent_create,
            snapshot=snapshot,
        )
    else:
        target = await session.get(AgentRecord, draft.target_agent_id)
        if target is None or target.workspace_id != workspace_id or target.system_purpose is not None:
            raise AuthorizationError("configuration_not_found", concealed=True)
        await authorize_persisted_agent_principal_actions(
            session,
            principal=principal,
            organization_id=organization_id,
            workspace_id=workspace_id,
            agent_id=target.id,
            actions=frozenset({WorkspaceAction.agent_read, WorkspaceAction.agent_revision_create}),
            snapshot=snapshot,
        )


async def authorize_invocation(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    agent_id: str,
    context: ConfigurationRunContext,
) -> AuthorizedWorkspace:
    """Authorize the stable draft binding of a protected assistant invocation."""
    conversation = await authorize_session(session, actor=actor, session_id=context.session_id)
    await authorize_execution(
        session,
        principal=actor.principal,
        organization_id=conversation.organization_id,
        workspace_id=conversation.workspace_id,
        agent_id=agent_id,
        context=context,
    )
    return AuthorizedWorkspace(conversation.organization_id, conversation.workspace_id, actor)
