"""Scoped Agent and Revision queries for invocation selection."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import (
    agent_archived,
    agent_disabled,
    agent_not_found,
    agent_revision_not_executable,
    agent_revision_not_found,
)
from ..models import AgentRecord, AgentRevisionRecord
from .contracts import (
    RootAgentStatePolicy,
)


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
        # Admission reads share a snapshot lock, excluding metadata edits while
        # allowing concurrent freezes and retained Runs' foreign-key checks.
        statement = statement.with_for_update(read=True)
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
        statement = statement.with_for_update(read=True)
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
    if child.current_revision_id is None:
        raise agent_revision_not_executable("subagent_revision_unavailable")
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
