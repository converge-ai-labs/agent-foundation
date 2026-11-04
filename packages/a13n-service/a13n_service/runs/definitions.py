"""Resolve the immutable execution source owned by a Thread and an accepted record.

Null revision IDs alone never identify snapshots. New acceptance checks the source
Agent head; workers load already accepted definitions without an archival check.
"""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import invalid
from a13n_service.infra.ids import ObjectId
from a13n_service.resources.agents import stored
from a13n_service.resources.agents.schemas import AgentConfig, AgentOverride
from a13n_service.resources.agents.service import SelectedRevision, load_revision, require_active_agent, select_revision
from a13n_service.runs.tables import RunRow, ThreadRow


class AgentSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    definition_id: ObjectId
    config: AgentConfig


@dataclass(frozen=True, slots=True)
class SelectedSnapshot:
    agent_id: str
    definition_id: str
    config: AgentConfig
    revision_id: None = None


type Definition = SelectedRevision | SelectedSnapshot


async def resolve(
    session: AsyncSession,
    thread: ThreadRow,
    agent_id: str,
    revision_id: str | None,
    overrides: AgentOverride | None,
    *,
    accepting: bool,
) -> Definition:
    if thread.agent_snapshot is not None:
        if revision_id is not None or overrides is not None:
            raise invalid(
                "agent_revision_id", "Snapshot threads cannot replace their execution source or configuration"
            )
        origin = await session.get_one(RunRow, thread.origin_run_id)
        if agent_id != origin.agent_id:
            raise invalid("agent_id", "Snapshot threads retain their source Agent")
        snapshot = stored.require(AgentSnapshot, thread.agent_snapshot, kind="thread", id=thread.id)
        if snapshot.config.subagents.named or snapshot.config.subagents.dynamic.enabled:
            raise invalid("subagents", "Snapshot agents cannot delegate")
        if accepting:
            await require_active_agent(session, thread.workspace_id, agent_id)
        return SelectedSnapshot(agent_id, snapshot.definition_id, snapshot.config)
    if accepting:
        return await select_revision(session, thread.workspace_id, agent_id, revision_id)
    if revision_id is None:
        raise invalid("agent_revision_id", "An accepted ordinary Run requires a published revision")
    return await load_revision(session, agent_id, revision_id)
