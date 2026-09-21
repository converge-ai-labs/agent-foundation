"""Revision publication inside the caller's Agent transaction."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam import AuthenticatedActor

from .domain import AgentConfig, ResolvedRevisionContent, new_agent_revision_id
from .models import AgentRecord, AgentRevisionRecord
from .persistence import lock_revision, new_revision, next_revision_number, touch_agent


@dataclass(frozen=True, slots=True)
class RevisionPublication:
    """Transaction-local result for entry-point receipts and audits."""

    revision: AgentRevisionRecord
    previous_revision_id: str

    @property
    def changed(self) -> bool:
        return self.revision.id != self.previous_revision_id


async def publish_revision(
    session: AsyncSession,
    *,
    locked_agent: AgentRecord,
    config: AgentConfig,
    resolved: ResolvedRevisionContent,
    source_revision_id: str | None,
    change_summary: str | None,
    actor: AuthenticatedActor,
    now: datetime,
) -> RevisionPublication:
    """Reuse the current content or append a Revision and advance the Agent head.

    The caller owns authorization, the Agent lock, preconditions, and dependency
    freezing. It also owns flush and commit so publication remains atomic with
    its receipt, audit, and any draft changes.
    """
    revision = new_revision(
        locked_agent,
        revision_id=new_agent_revision_id(),
        version=await next_revision_number(session, locked_agent.id),
        config=config,
        resolved=resolved,
        source_revision_id=source_revision_id,
        change_summary=change_summary,
        actor=actor,
        now=now,
    )
    assert locked_agent.default_revision_id is not None
    current = await lock_revision(
        session,
        organization_id=locked_agent.organization_id,
        workspace_id=locked_agent.workspace_id,
        agent_id=locked_agent.id,
        revision_id=locked_agent.default_revision_id,
    )
    if current.content_digest == revision.content_digest:
        return RevisionPublication(current, current.id)
    session.add(revision)
    locked_agent.default_revision_id = revision.id
    touch_agent(locked_agent, actor=actor, now=now)
    return RevisionPublication(revision, current.id)
