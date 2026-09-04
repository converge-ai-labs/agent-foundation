"""Agent revision graph loading, validation, and freezing."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam import (
    WorkspaceAction,
    authorize_agent,
)

from ..errors import (
    agent_archived,
    agent_disabled,
    agent_not_found,
    agent_revision_not_executable,
    agent_revision_not_found,
)
from ..models import AgentRecord, AgentRevisionRecord
from ..resolution import MAX_SUBAGENT_DEPTH, MAX_SUBAGENT_NODES
from .contracts import (
    PreparedAgentInvocation,
    PreparedInvocationSubagent,
    RootAgentStatePolicy,
)


async def freeze_subagents(
    session: AsyncSession,
    prepared: PreparedAgentInvocation,
) -> tuple[PreparedInvocationSubagent, ...]:
    result: list[PreparedInvocationSubagent] = []
    for expected in prepared.subagents:
        await authorize_agent(
            session,
            actor=prepared.actor,
            workspace_id=prepared.workspace_id,
            agent_id=expected.edge.child_agent_id,
            action=WorkspaceAction.agent_invoke,
        )
        child = await load_agent_record(
            session,
            organization_id=prepared.organization_id,
            workspace_id=prepared.workspace_id,
            agent_id=expected.edge.child_agent_id,
            for_update=True,
        )
        require_invocable_agent(child)
        revision = await load_revision_record(
            session,
            organization_id=prepared.organization_id,
            workspace_id=prepared.workspace_id,
            agent_id=child.id,
            revision_id=expected.edge.child_agent_revision_id,
            for_update=True,
        )
        if (
            revision.content_digest != expected.child_revision_digest
            or revision.runtime_lock_digest != expected.child_runtime_lock_digest
        ):
            raise agent_revision_not_executable("subagent_revision_changed")
        result.append(expected)
    return tuple(result)


async def load_agent_record(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    agent_id: str,
    for_update: bool,
) -> AgentRecord:
    statement = select(AgentRecord).where(
        AgentRecord.id == agent_id,
        AgentRecord.organization_id == organization_id,
        AgentRecord.workspace_id == workspace_id,
    )
    if for_update:
        statement = statement.with_for_update()
    record = await session.scalar(statement)
    if record is None:
        raise agent_not_found()
    return record


async def load_revision_record(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    agent_id: str,
    revision_id: str,
    for_update: bool,
) -> AgentRevisionRecord:
    statement = select(AgentRevisionRecord).where(
        AgentRevisionRecord.id == revision_id,
        AgentRevisionRecord.agent_id == agent_id,
        AgentRevisionRecord.organization_id == organization_id,
        AgentRevisionRecord.workspace_id == workspace_id,
    )
    if for_update:
        statement = statement.with_for_update()
    record = await session.scalar(statement)
    if record is None:
        raise agent_revision_not_found()
    return record


def require_invocable_agent(
    agent: AgentRecord,
    *,
    policy: RootAgentStatePolicy = RootAgentStatePolicy.invocable,
) -> None:
    if not agent.enabled and policy is RootAgentStatePolicy.invocable:
        raise agent_disabled()
    if agent.archived_at is not None and policy is not RootAgentStatePolicy.archived_allowed:
        raise agent_archived()


async def select_child_revision_id(
    session: AsyncSession,
    *,
    child: AgentRecord,
    organization_id: str,
    workspace_id: str,
    version: int | None,
) -> str:
    if version is None:
        return child.current_revision_id
    revision_id = await session.scalar(
        select(AgentRevisionRecord.id).where(
            AgentRevisionRecord.agent_id == child.id,
            AgentRevisionRecord.organization_id == organization_id,
            AgentRevisionRecord.workspace_id == workspace_id,
            AgentRevisionRecord.version == version,
        )
    )
    if revision_id is None:
        raise agent_revision_not_executable("subagent_revision_unavailable")
    return revision_id


async def validate_subagent_graph(
    session: AsyncSession,
    *,
    root_agent_id: str,
    first_revision: AgentRevisionRecord,
) -> None:
    pending: list[tuple[AgentRevisionRecord, int]] = [(first_revision, 1)]
    visited: set[str] = set()
    while pending:
        revision, depth = pending.pop()
        if depth > MAX_SUBAGENT_DEPTH:
            raise agent_revision_not_executable("subagent_graph_too_deep")
        if revision.agent_id == root_agent_id:
            raise agent_revision_not_executable("subagent_cycle")
        if revision.id in visited:
            continue
        visited.add(revision.id)
        if len(visited) > MAX_SUBAGENT_NODES:
            raise agent_revision_not_executable("subagent_graph_too_large")
        child_ids = tuple(item["child_agent_revision_id"] for item in revision.resolved_subagents)
        if not child_ids:
            continue
        children = tuple(
            (await session.scalars(select(AgentRevisionRecord).where(AgentRevisionRecord.id.in_(child_ids)))).all()
        )
        if len(children) != len(set(child_ids)):
            raise agent_revision_not_executable("subagent_revision_unavailable")
        pending.extend((child, depth + 1) for child in children)
