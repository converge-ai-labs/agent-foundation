"""Derive immutable Agent revisions without changing the default."""

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import conflict, not_found
from a13n_service.infra.http import etag
from a13n_service.resources.agents import service
from a13n_service.resources.agents.schemas import Agent, AgentConfig, AgentRevision, AgentRevisionCreate
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.authorize import authorize

from ._access import app_actor, app_workspace


async def get_agent_default_revision(
    session: AsyncSession, workspace_id: str, agent_id: str
) -> tuple[Agent, AgentRevision]:
    """Read the immutable default revision of an App-authorized Agent."""
    head = await service.resolve_agent(session, workspace_id, agent_id)
    if head.default_revision_id is None:
        raise not_found("agent_revision", head.id)
    revision = await service._revision(session, head, head.default_revision_id)
    return service.agent_view(head), service._revision_view(head, revision)


async def publish_derived_revision(
    session: AsyncSession,
    runtime: Runtime,
    workspace_id: str,
    *,
    app_id: str,
    actor_id: str,
    agent_id: str,
    mutate_config: Callable[[AgentConfig], AgentConfig],
    note: str | None,
) -> AgentRevision:
    """Apply a synchronous transformation to the locked current default and publish if changed."""
    scope = await app_workspace(session, runtime, workspace_id, app_id)
    actor = await app_actor(session, runtime, scope, actor_id)
    authorize(actor, scope, "write")
    head = await service.resolve_agent(session, workspace_id, agent_id, lock=True)
    if head.default_revision_id is None:
        raise not_found("agent_revision", head.id)
    current = service._revision_view(head, await service._revision(session, head, head.default_revision_id))
    config = mutate_config(current.config.model_copy(deep=True))
    if config == current.config:
        return current
    if head.builtin:
        raise conflict(head.KIND, head.id, "builtin")
    return await service.publish_revision(
        session,
        actor,
        workspace_id,
        head.id,
        AgentRevisionCreate(config=config, note=note, make_default=False),
        if_match=etag(head.id, head.version),
        registry=runtime.registry,
        plugins=runtime.plugins,
    )
