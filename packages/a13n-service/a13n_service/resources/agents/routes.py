"""Agent heads, their avatars and revisions, and the built-in toolset catalogue their configurations select from."""

from typing import Annotated

from fastapi import APIRouter, Query, Response

from a13n_service.infra import images
from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.resources.agents import composer, service
from a13n_service.resources.agents.schemas import (
    Agent,
    AgentCreate,
    AgentDuplicate,
    AgentPage,
    AgentRevision,
    AgentRevisionCreate,
    AgentRevisionPage,
    AgentUpdate,
    AgentValidate,
)
from a13n_service.resources.agents.toolsets import ToolsetCatalog
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.resources.revisions import Search
from a13n_service.tenancy.requests import Actor, ImageBody

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}", tags=["agents"])


@router.post("/agents", response_model=Agent, status_code=201)
async def create_agent(
    response: Response, workspace_id: str, body: AgentCreate, actor: Actor, runtime: CurrentRuntime
) -> Agent:
    created = await service.create_agent(
        runtime.storage, actor, workspace_id, body, registry=runtime.registry, plugins=runtime.plugins
    )
    return tagged(response, created)


@router.post("/agents/validate", status_code=204)
async def validate_revision(workspace_id: str, body: AgentValidate, actor: Actor, runtime: CurrentRuntime) -> None:
    """No content when creating a revision of the configuration would accept it, else the same `invalid_argument`
    error with the field's path relative to `config`; nothing is stored."""
    await service.validate_revision(
        runtime.storage, actor, workspace_id, body, registry=runtime.registry, plugins=runtime.plugins
    )


@router.post("/agent-composer", response_model=Agent)
async def prepare_composer(response: Response, workspace_id: str, actor: Actor, runtime: CurrentRuntime) -> Agent:
    """The workspace's Agent Composer, created or brought up to date with the deployment's definition.

    Refused with `model_required` while the workspace has no model the caller can use.
    """
    prepared = await composer.prepare(
        runtime.storage,
        actor,
        workspace_id,
        preferred=runtime.settings.composer.models,
        registry=runtime.registry,
        plugins=runtime.plugins,
    )
    return tagged(response, prepared)


@router.get("/agents", response_model=AgentPage)
async def list_agents(
    workspace_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    label: Annotated[list[str] | None, Query()] = None,
    q: Annotated[Search | None, Query()] = None,
    archived: bool | None = None,
    skill_id: Annotated[str | None, Query(max_length=72)] = None,
    skill_revision_id: Annotated[str | None, Query(max_length=72)] = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> AgentPage:
    """Agents of the workspace. `q` matches the key, name or description, ignoring case; `archived` keeps only
    archived agents, or only open ones; the skill filters keep those with a revision pinning that skill or
    revision."""
    return await service.list_agents(
        runtime.storage,
        actor,
        workspace_id,
        labels=label or [],
        q=q,
        archived=archived,
        skill_id=skill_id,
        skill_revision_id=skill_revision_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/agents/{agent_id}", response_model=Agent)
async def get_agent(
    response: Response, workspace_id: str, agent_id: str, actor: Actor, runtime: CurrentRuntime
) -> Agent:
    return tagged(response, await service.get_agent(runtime.storage, actor, workspace_id, agent_id))


@router.patch("/agents/{agent_id}", response_model=Agent)
async def update_agent(
    response: Response,
    workspace_id: str,
    agent_id: str,
    body: AgentUpdate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Agent:
    updated = await service.update_agent(runtime.storage, actor, workspace_id, agent_id, body, if_match=if_match)
    return tagged(response, updated)


@router.put("/agents/{agent_id}/avatar", response_model=Agent, openapi_extra=images.UPLOAD)
async def put_avatar(
    response: Response,
    workspace_id: str,
    agent_id: str,
    data: ImageBody,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Agent:
    changed = await service.change_avatar(
        runtime.storage, runtime.objects, actor, workspace_id, agent_id, data, if_match=if_match
    )
    return tagged(response, changed)


@router.delete("/agents/{agent_id}/avatar", response_model=Agent)
async def delete_avatar(
    response: Response,
    workspace_id: str,
    agent_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Agent:
    changed = await service.change_avatar(
        runtime.storage, runtime.objects, actor, workspace_id, agent_id, None, if_match=if_match
    )
    return tagged(response, changed)


@router.get("/agents/{agent_id}/avatar", response_class=Response, responses=images.CONTENT)
async def get_avatar(workspace_id: str, agent_id: str, actor: Actor, runtime: CurrentRuntime) -> Response:
    image = await service.get_avatar(runtime.storage, actor, workspace_id, agent_id)
    return await images.serve(runtime.objects, agent_id, image)


@router.post("/agents/{agent_id}/archive", response_model=Agent)
async def archive_agent(
    response: Response,
    workspace_id: str,
    agent_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Agent:
    archived = await service.set_archived(
        runtime.storage, actor, workspace_id, agent_id, archived=True, if_match=if_match
    )
    return tagged(response, archived)


@router.post("/agents/{agent_id}/unarchive", response_model=Agent)
async def unarchive_agent(
    response: Response,
    workspace_id: str,
    agent_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Agent:
    restored = await service.set_archived(
        runtime.storage, actor, workspace_id, agent_id, archived=False, if_match=if_match
    )
    return tagged(response, restored)


@router.post("/agents/{agent_id}/duplicate", response_model=Agent, status_code=201)
async def duplicate_agent(
    response: Response, workspace_id: str, agent_id: str, body: AgentDuplicate, actor: Actor, runtime: CurrentRuntime
) -> Agent:
    created = await service.duplicate_agent(
        runtime.storage, actor, workspace_id, agent_id, body, registry=runtime.registry, plugins=runtime.plugins
    )
    return tagged(response, created)


@router.post("/agents/{agent_id}/revisions", response_model=AgentRevision, status_code=201)
async def create_revision(
    workspace_id: str,
    agent_id: str,
    body: AgentRevisionCreate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> AgentRevision:
    """A configuration that validates to the default revision's creates nothing and returns that revision."""
    return await service.create_revision(
        runtime.storage,
        actor,
        workspace_id,
        agent_id,
        body,
        if_match=if_match,
        registry=runtime.registry,
        plugins=runtime.plugins,
    )


@router.get("/agents/{agent_id}/revisions", response_model=AgentRevisionPage)
async def list_revisions(
    workspace_id: str,
    agent_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> AgentRevisionPage:
    return await service.list_revisions(runtime.storage, actor, workspace_id, agent_id, limit=limit, cursor=cursor)


@router.get("/agents/{agent_id}/revisions/{revision_id}", response_model=AgentRevision)
async def get_revision(
    workspace_id: str, agent_id: str, revision_id: str, actor: Actor, runtime: CurrentRuntime
) -> AgentRevision:
    return await service.get_revision(runtime.storage, actor, workspace_id, agent_id, revision_id)


@router.post("/agents/{agent_id}/revisions/{revision_id}/set-default", response_model=Agent)
async def set_default(
    response: Response,
    workspace_id: str,
    agent_id: str,
    revision_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Agent:
    updated = await service.set_default(
        runtime.storage,
        actor,
        workspace_id,
        agent_id,
        revision_id,
        if_match=if_match,
        registry=runtime.registry,
        plugins=runtime.plugins,
    )
    return tagged(response, updated)


@router.get("/toolsets", response_model=ToolsetCatalog)
async def list_toolsets(workspace_id: str, actor: Actor, runtime: CurrentRuntime) -> ToolsetCatalog:
    return await service.toolset_catalog(runtime.storage, actor, workspace_id, registry=runtime.registry)
