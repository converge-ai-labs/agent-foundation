"""Current Agent references that govern Skill deletion."""

from __future__ import annotations

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.domain import ResolvedSkillBinding
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord

from .domain import SkillAgentReference

_BINDINGS_ADAPTER = TypeAdapter(tuple[ResolvedSkillBinding, ...])


async def current_agent_references(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    skill_id: str,
) -> tuple[SkillAgentReference, ...]:
    """Return current unarchived Agent heads that bind one stable Skill identity."""

    rows = tuple(
        (
            await session.execute(
                select(AgentRecord, AgentRevisionRecord)
                .join(AgentRevisionRecord, AgentRevisionRecord.id == AgentRecord.default_revision_id)
                .where(
                    AgentRecord.organization_id == organization_id,
                    AgentRecord.workspace_id == workspace_id,
                    AgentRecord.archived_at.is_(None),
                    AgentRevisionRecord.organization_id == organization_id,
                    AgentRevisionRecord.workspace_id == workspace_id,
                )
                .order_by(AgentRecord.name, AgentRecord.id)
            )
        ).all()
    )
    return tuple(
        SkillAgentReference(
            agent_id=agent.id,
            agent_revision_id=revision.id,
            agent_name=agent.name,
            agent_key=agent.key,
        )
        for agent, revision in rows
        if any(binding.skill_id == skill_id for binding in _BINDINGS_ADAPTER.validate_python(revision.resolved_skills))
    )
