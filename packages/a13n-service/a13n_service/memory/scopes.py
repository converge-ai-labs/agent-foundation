"""Trusted Workspace/subject bindings shared by control and worker."""

import hashlib
import json
from typing import Literal

from a13n_harness.capabilities.mem0 import Mem0Scope
from a13n_harness.capabilities.mem0_backends import Mem0Subject
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


def memory_subject(organization_id: str, workspace_id: str, scope: Mem0Scope, subject_id: str) -> Mem0Subject:
    # Length-delimited canonical input prevents tenant/subject concatenation collisions.
    namespace = json.dumps(
        ["a13n.memory.v1", organization_id, workspace_id, scope.value, subject_id], separators=(",", ":")
    )
    fields: dict[Mem0Scope, Literal["run_id", "agent_id", "user_id"]] = {
        Mem0Scope.THREAD: "run_id",
        Mem0Scope.AGENT: "agent_id",
        Mem0Scope.USER: "user_id",
    }
    return Mem0Subject(fields[scope], "a13n-" + hashlib.sha256(namespace.encode()).hexdigest())


class MemoryAuthorizer:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def authorize(
        self, *, actor: AuthenticatedActor, workspace_id: str, selection: MemoryScope, write: bool = False
    ) -> Mem0Subject:
        action = WorkspaceAction.memory_write if write else WorkspaceAction.memory_read
        async with short_session(self.sessions) as session:
            subject_id = selection.subject_id
            agent_id = None
            organization_id = None
            if selection.scope is Mem0Scope.USER:
                if actor.principal.principal_type is not PrincipalType.user:
                    raise AuthorizationError("memory_scope_unavailable", concealed=True)
                subject_id = actor.principal.principal_id
            elif selection.scope is Mem0Scope.AGENT:
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
            return memory_subject(authorized.organization_id, workspace_id, selection.scope, subject_id)
