"""Agent creation and Revision publication inside the caller's transaction."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam import AuthenticatedActor
from a13n_service.resource_keys import insert_with_key

from .domain import AgentConfig, ResolvedRevisionContent, new_agent_revision_id
from .models import AgentRecord, AgentRevisionRecord
from .persistence import lock_revision, new_revision, next_revision_number, touch_agent


async def create_agent(
    session: AsyncSession,
    *,
    agent_id: str,
    organization_id: str,
    workspace_id: str,
    name: str,
    description: str | None,
    labels: dict[str, str],
    config: AgentConfig,
    resolved: ResolvedRevisionContent,
    actor: AuthenticatedActor,
    now: datetime,
    requested_key: str | None = None,
    request_key: str | None = None,
    source_revision_id: str | None = None,
    change_summary: str | None = None,
) -> tuple[AgentRecord, AgentRevisionRecord]:
    """Insert a custom Agent and stage its first default Revision.

    The caller owns authorization, dependency freezing, replay, and commit.
    Key allocation flushes the Agent inside a savepoint; the Revision and any
    caller-owned receipt or audit remain part of the same outer transaction.
    """
    revision_id = new_agent_revision_id()
    agent = AgentRecord(
        id=agent_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        source="custom",
        name=name,
        description=description,
        labels=labels,
        default_revision_id=revision_id,
        enabled=True,
        archived_at=None,
        duplicated_from_agent_id=None,
        duplicated_from_revision_id=None,
        created_by_type=actor.principal.principal_type.value,
        created_by_id=actor.principal.principal_id,
        updated_by_type=actor.principal.principal_type.value,
        updated_by_id=actor.principal.principal_id,
        created_at=now,
        updated_at=now,
        request_key=request_key,
    )
    await insert_with_key(session, agent, prefix="agent", requested=requested_key)
    revision = new_revision(
        agent,
        revision_id=revision_id,
        version=1,
        config=config,
        resolved=resolved,
        source_revision_id=source_revision_id,
        change_summary=change_summary,
        actor=actor,
        now=now,
    )
    session.add(revision)
    return agent, revision


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
