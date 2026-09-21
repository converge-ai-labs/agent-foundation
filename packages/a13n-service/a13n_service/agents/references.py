"""Visible Agent Revision references to managed Web and Memory Providers."""

from typing import Literal, TypedDict

from sqlalchemy import cast, or_, select, tuple_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.collection_cursors import InvalidCollectionCursorError, encode_collection_cursor
from a13n_service.iam import AuthenticatedActor, authorize_agent
from a13n_service.iam.authorization import AuthorizationError, WorkspaceAction

from .models import AgentRecord, AgentRevisionRecord


class ProviderReference(TypedDict):
    agent_id: str
    agent_revision_id: str
    version: int
    is_current: bool


async def provider_references(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str | None,
    provider_kind: Literal["web", "memory"],
    provider_id: str,
    limit: int,
    position: dict[str, object] | None,
    scope_key: dict[str, object],
) -> tuple[tuple[ProviderReference, ...], str | None]:
    """Query retained revisions after the caller authorizes access to the provider."""

    config = AgentRevisionRecord.config
    if provider_kind == "web":
        tools = config["toolsets"]["web"]["tools"]
        predicate = or_(
            tools["search"]["config"]["provider_id"].as_string() == provider_id,
            tools["scrape"]["config"]["provider_id"].as_string() == provider_id,
        )
    else:
        predicate = or_(
            config["memory"]["provider_id"].as_string() == provider_id,
            cast(config, JSONB)["memory"]["entries"].contains([{"backend": {"provider_id": provider_id}}]),
        )
    query = (
        select(AgentRevisionRecord, AgentRecord)
        .join(AgentRecord, AgentRecord.id == AgentRevisionRecord.agent_id)
        .where(
            AgentRecord.organization_id == organization_id,
            predicate,
        )
    )
    if workspace_id is not None:
        query = query.where(AgentRecord.workspace_id == workspace_id)
    if position is not None:
        agent_id, revision_id = position.get("name"), position.get("id")
        if not isinstance(agent_id, str) or not isinstance(revision_id, str):
            raise InvalidCollectionCursorError
        version = position.get("version")
        if type(version) is not int or version < 1:
            raise InvalidCollectionCursorError
        query = query.where(
            tuple_(AgentRevisionRecord.agent_id, AgentRevisionRecord.version, AgentRevisionRecord.id)
            > (agent_id, version, revision_id)
        )
    query = query.order_by(AgentRevisionRecord.agent_id, AgentRevisionRecord.version, AgentRevisionRecord.id)
    items: list[ProviderReference] = []
    # Page in bounded batches, applying Agent visibility before counting.
    offset = 0
    while len(items) <= limit:
        rows = (await session.execute(query.offset(offset).limit(100))).all()
        if not rows:
            break
        for revision, agent in rows:
            try:
                await authorize_agent(
                    session,
                    actor=actor,
                    workspace_id=agent.workspace_id,
                    agent_id=agent.id,
                    action=WorkspaceAction.agent_read,
                )
            except AuthorizationError:
                continue
            items.append(
                ProviderReference(
                    agent_id=agent.id,
                    agent_revision_id=revision.id,
                    version=revision.version,
                    is_current=agent.default_revision_id == revision.id,
                )
            )
            if len(items) > limit:
                break
        offset += len(rows)
    page = items[:limit]
    next_cursor = None
    if len(items) > limit:
        last = page[-1]
        next_cursor = encode_collection_cursor(
            {"name": last["agent_id"], "id": last["agent_revision_id"], "version": last["version"]}, scope=scope_key
        )
    return tuple(page), next_cursor
