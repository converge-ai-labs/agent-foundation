"""Trusted Workspace/subject bindings shared by control and worker."""

import hashlib
import json

from a13n_harness.memory import MemoryScope as ScopeKind
from a13n_harness.memory import MemorySubject
from a13n_harness.memory_plugins import MemoryBackendCatalog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.models import AgentRecord
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_agent,
    authorize_workspace,
)
from a13n_service.iam.domain import PrincipalType
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.storage import short_session

from .domain import MemoryScope
from .execution import MemoryProviderAccess
from .resources import require_provider


def memory_subject(
    organization_id: str, workspace_id: str, provider_id: str, scope: ScopeKind, subject_id: str
) -> MemorySubject:
    # Length-delimited canonical input prevents tenant/subject concatenation collisions.
    namespace = json.dumps(
        ["a13n.memory.v2", organization_id, workspace_id, provider_id, scope.value, subject_id], separators=(",", ":")
    )
    return MemorySubject(scope, "a13n-" + hashlib.sha256(namespace.encode()).hexdigest())


class MemoryAuthorizer:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], catalog: MemoryBackendCatalog) -> None:
        self.sessions = sessions
        self.catalog = catalog

    async def authorize(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        provider_id: str,
        selection: MemoryScope,
        write: bool = False,
    ) -> tuple[MemorySubject, MemoryProviderAccess]:
        async with short_session(self.sessions) as session:
            subject, organization_id = await authorize_memory_subject(
                session,
                actor=actor,
                workspace_id=workspace_id,
                provider_id=provider_id,
                selection=selection,
                write=write,
            )
            provider = await require_provider(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                provider_id=provider_id,
                eligible=True,
                catalog=self.catalog,
            )
            return (
                subject,
                MemoryProviderAccess.from_record(provider),
            )


async def authorize_memory_subject(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    provider_id: str,
    selection: MemoryScope,
    write: bool = False,
) -> tuple[MemorySubject, str]:
    action = WorkspaceAction.memory_write if write else WorkspaceAction.memory_read
    subject_id = selection.subject_id
    agent_id = None
    organization_id = None
    if selection.scope is ScopeKind.USER:
        if actor.principal.principal_type is not PrincipalType.user:
            raise AuthorizationError("memory_scope_unavailable", concealed=True)
        subject_id = actor.principal.principal_id
    elif selection.scope is ScopeKind.AGENT:
        agent = await session.get(AgentRecord, subject_id)
        if agent is None or agent.workspace_id != workspace_id:
            raise AuthorizationError("memory_scope_unavailable", concealed=True)
        agent_id, organization_id = agent.id, agent.organization_id
    else:
        thread = await session.get(ThreadRecord, subject_id)
        parent = await session.get(SessionRecord, thread.session_id) if thread is not None else None
        if (
            thread is None
            or parent is None
            or parent.workspace_id != workspace_id
            or parent.organization_id != thread.organization_id
        ):
            raise AuthorizationError("memory_scope_unavailable", concealed=True)
        organization_id = thread.organization_id
        run = await session.get(RunRecord, thread.current_run_id) if thread.current_run_id is not None else None
        if run is not None:
            if run.organization_id != organization_id or run.thread_id != thread.id:
                raise AuthorizationError("memory_scope_unavailable", concealed=True)
            agent_id = run.agent_id
    if agent_id is None:
        authorized = await authorize_workspace(session, actor=actor, workspace_id=workspace_id, action=action)
    else:
        authorized = await authorize_agent(
            session, actor=actor, workspace_id=workspace_id, agent_id=agent_id, action=action
        )
    if organization_id is not None and organization_id != authorized.organization_id:
        raise AuthorizationError("memory_scope_unavailable", concealed=True)
    assert subject_id is not None
    return memory_subject(
        authorized.organization_id, workspace_id, provider_id, selection.scope, subject_id
    ), authorized.organization_id
