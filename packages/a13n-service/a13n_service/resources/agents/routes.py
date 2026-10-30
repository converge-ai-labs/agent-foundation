"""Agent heads, their avatars and revisions, and the built-in toolset catalogue their configurations select from."""

from typing import Annotated

from fastapi import APIRouter, Query, Response

from a13n_service.infra import images, reads
from a13n_service.infra.http import IfMatch, PageLimit, download_headers, tagged
from a13n_service.infra.platform import platform_operation
from a13n_service.resources import catalog_reads
from a13n_service.resources.agents import composer, read_views, service
from a13n_service.resources.agents.plugins import PluginCatalog
from a13n_service.resources.agents.schemas import (
    Agent,
    AgentCreate,
    AgentDuplicate,
    AgentPage,
    AgentRevision,
    AgentRevisionCreate,
    AgentRevisionPage,
    AgentSource,
    AgentUpdate,
    AgentValidate,
)
from a13n_service.resources.agents.tables import PresetKind
from a13n_service.resources.agents.toolsets import ToolsetCatalog
from a13n_service.resources.builtin_skills import BuiltinSkillCatalog
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.resources.revisions import Search
from a13n_service.resources.skills import builtin
from a13n_service.resources.skills.schemas import BuiltinSkillDetail
from a13n_service.resources.uploads.schemas import Digest
from a13n_service.tenancy.requests import Actor, ImageBody, WorkspaceId

router = APIRouter(prefix="/api/v1", tags=["agents"])


@router.post("/agents", response_model=Agent, status_code=201, openapi_extra=platform_operation("agents", "write"))
async def create_agent(
    response: Response, workspace_id: WorkspaceId, body: AgentCreate, actor: Actor, runtime: CurrentRuntime
) -> Agent:
    created = await service.create_agent(
        runtime.storage, actor, workspace_id, body, registry=runtime.registry, plugins=runtime.plugins
    )
    return tagged(response, created)


@router.post("/agents/validate", status_code=204, openapi_extra=platform_operation("agents", "read"))
async def validate_agent(workspace_id: WorkspaceId, body: AgentValidate, actor: Actor, runtime: CurrentRuntime) -> None:
    """No content when creating a revision of the configuration would accept it, else the same `invalid_argument`
    error with the field's path relative to `config`; nothing is stored."""
    await service.validate_revision(
        runtime.storage, actor, workspace_id, body, registry=runtime.registry, plugins=runtime.plugins
    )


@router.post("/agent-composer", response_model=Agent, openapi_extra={"x-a13n-mcp": True})
async def prepare_composer(
    response: Response, workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime
) -> Agent:
    """The workspace's Agent Composer, initialized or offered an updated official revision without changing its default.

    Initial preparation requires a usable model; later preparation preserves the selected model and revision.
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


@router.get("/agents", response_model=AgentPage, openapi_extra=platform_operation("agents", "read"))
async def list_agents(
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
    label: Annotated[list[str] | None, Query()] = None,
    q: Annotated[Search | None, Query()] = None,
    archived: bool | None = None,
    source: AgentSource | None = None,
    preset_kind: PresetKind | None = None,
    skill_id: Annotated[str | None, Query(max_length=72)] = None,
    skill_revision_id: Annotated[str | None, Query(max_length=72)] = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> AgentPage:
    """Agents of the workspace. `q` matches the name or description, ignoring case; `archived` keeps only
    archived agents, or only open ones; `source=builtin` finds managed presets; the skill filters keep those
    with a revision pinning that skill or that skill revision."""
    return await service.list_agents(
        runtime.storage,
        actor,
        workspace_id,
        labels=label or [],
        q=q,
        archived=archived,
        source=source,
        preset_kind=preset_kind,
        skill_id=skill_id,
        skill_revision_id=skill_revision_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/agents/{agent_id}", response_model=Agent, openapi_extra=platform_operation("agents", "read"))
async def get_agent(
    response: Response, workspace_id: WorkspaceId, agent_id: str, actor: Actor, runtime: CurrentRuntime
) -> Agent:
    return tagged(response, await service.get_agent(runtime.storage, actor, workspace_id, agent_id))


@router.patch("/agents/{agent_id}", response_model=Agent, openapi_extra=platform_operation("agents", "write"))
async def update_agent(
    response: Response,
    workspace_id: WorkspaceId,
    agent_id: str,
    body: AgentUpdate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Agent:
    updated = await service.update_agent(runtime.storage, actor, workspace_id, agent_id, body, if_match=if_match)
    return tagged(response, updated)


@router.put("/agents/{agent_id}/avatar", response_model=Agent, openapi_extra=images.UPLOAD)
async def put_agent_avatar(
    response: Response,
    workspace_id: WorkspaceId,
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


@router.delete("/agents/{agent_id}/avatar", response_model=Agent, openapi_extra={"x-a13n-mcp": True})
async def delete_agent_avatar(
    response: Response,
    workspace_id: WorkspaceId,
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
async def get_agent_avatar(workspace_id: WorkspaceId, agent_id: str, actor: Actor, runtime: CurrentRuntime) -> Response:
    image = await service.get_avatar(runtime.storage, actor, workspace_id, agent_id)
    return await images.serve(runtime.objects, agent_id, image)


@router.post("/agents/{agent_id}/archive", response_model=Agent, openapi_extra=platform_operation("agents", "write"))
async def archive_agent(
    response: Response,
    workspace_id: WorkspaceId,
    agent_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Agent:
    archived = await service.set_archived(
        runtime.storage, actor, workspace_id, agent_id, archived=True, if_match=if_match
    )
    return tagged(response, archived)


@router.post("/agents/{agent_id}/unarchive", response_model=Agent, openapi_extra=platform_operation("agents", "write"))
async def unarchive_agent(
    response: Response,
    workspace_id: WorkspaceId,
    agent_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Agent:
    restored = await service.set_archived(
        runtime.storage, actor, workspace_id, agent_id, archived=False, if_match=if_match
    )
    return tagged(response, restored)


@router.post(
    "/agents/{agent_id}/duplicate",
    response_model=Agent,
    status_code=201,
    openapi_extra=platform_operation("agents", "write"),
)
async def duplicate_agent(
    response: Response,
    workspace_id: WorkspaceId,
    agent_id: str,
    body: AgentDuplicate,
    actor: Actor,
    runtime: CurrentRuntime,
) -> Agent:
    created = await service.duplicate_agent(
        runtime.storage, actor, workspace_id, agent_id, body, registry=runtime.registry, plugins=runtime.plugins
    )
    return tagged(response, created)


@router.post(
    "/agents/{agent_id}/revisions",
    response_model=AgentRevision,
    status_code=201,
    openapi_extra=platform_operation("agents", "write"),
)
async def create_agent_revision(
    workspace_id: WorkspaceId,
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


@router.get(
    "/agents/{agent_id}/revisions",
    response_model=AgentRevisionPage | read_views.RevisionSummaryPage,
    openapi_extra=platform_operation("agents", "read"),
)
async def list_agent_revisions(
    workspace_id: WorkspaceId,
    agent_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    view: reads.View = "detailed",
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    cursor: str | None = None,
) -> AgentRevisionPage | read_views.RevisionSummaryPage:
    if view == "compact":
        return await service.list_revision_summaries(
            runtime.storage, actor, workspace_id, agent_id, limit=reads.page_limit(view, limit), cursor=cursor
        )
    return await service.list_revisions(
        runtime.storage, actor, workspace_id, agent_id, limit=reads.page_limit(view, limit), cursor=cursor
    )


@router.get(
    "/agents/{agent_id}/revisions/{revision_id}",
    response_model=AgentRevision | read_views.RevisionSummary | reads.TextRead,
    openapi_extra=platform_operation("agents", "read"),
)
async def get_agent_revision(
    workspace_id: WorkspaceId,
    agent_id: str,
    revision_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    view: reads.View = "detailed",
    section: read_views.ConfigSection | None = None,
    offset: reads.Offset = 0,
    limit: reads.TextLimit = 4000,
    version: reads.Version = None,
    query: reads.Search = None,
) -> AgentRevision | read_views.RevisionSummary | reads.TextRead:
    value = await service.get_revision(runtime.storage, actor, workspace_id, agent_id, revision_id)
    if section is not None:
        return reads.read_value(
            read_views.config_section(value.config, section),
            source=f"agents/{agent_id}/revisions/{revision_id}/config/{section}",
            offset=offset,
            limit=limit,
            version=version,
            query=query,
        )
    if offset or version or query:
        from a13n_service.infra.errors import invalid

        raise invalid("section", "Select a configuration section for fragment or search parameters")
    if view == "compact":
        head = await service.get_agent(runtime.storage, actor, workspace_id, agent_id)
        return read_views.revision_summary(value, head.default_revision_id)
    return value


@router.post(
    "/agents/{agent_id}/revisions/{revision_id}/set-default",
    response_model=Agent,
    openapi_extra=platform_operation("agents", "write"),
)
async def set_default_agent_revision(
    response: Response,
    workspace_id: WorkspaceId,
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


@router.get("/toolsets", response_model=ToolsetCatalog, openapi_extra=platform_operation("agents", "read"))
async def list_toolsets(workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime) -> ToolsetCatalog:
    return await service.toolset_catalog(runtime.storage, actor, workspace_id, registry=runtime.registry)


@router.get("/plugins", response_model=PluginCatalog, openapi_extra={"x-a13n-mcp": True})
async def list_plugins(workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime) -> PluginCatalog:
    """The plugins loaded by this Control process, without constructing or executing instances."""
    return await service.plugin_catalog(runtime.storage, actor, workspace_id, plugins=runtime.plugins)


@router.get("/builtin-skills", response_model=BuiltinSkillCatalog, openapi_extra={"x-a13n-mcp": True})
async def list_builtin_skills(workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime) -> BuiltinSkillCatalog:
    """The skills bundled with this Service release, separately from workspace skill resources."""
    return await service.builtin_skill_catalog(runtime.storage, actor, workspace_id, skills=runtime.builtin_skills)


@router.get("/builtin-skills/{name}", response_model=BuiltinSkillDetail, openapi_extra={"x-a13n-mcp": True})
async def get_builtin_skill(
    workspace_id: WorkspaceId, name: str, actor: Actor, runtime: CurrentRuntime
) -> BuiltinSkillDetail:
    """The current deployment's bundled skill and its package file manifest."""
    return await builtin.get_skill(runtime.storage, actor, workspace_id, name, skills=runtime.builtin_skills)


@router.get(
    "/builtin-skills/{name}/content",
    response_class=Response,
    responses={
        200: {
            "description": "The current builtin skill package as a zip archive",
            "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def read_builtin_skill_archive(
    workspace_id: WorkspaceId,
    name: str,
    actor: Actor,
    runtime: CurrentRuntime,
    package_digest: Digest | None = None,
) -> Response:
    """Optionally require the manifest's digest; a changed package returns conflict."""
    content = await builtin.read_archive(
        runtime.storage,
        actor,
        workspace_id,
        name,
        skills=runtime.builtin_skills,
        package_digest=package_digest,
    )
    return Response(content, media_type="application/zip", headers=download_headers(f"{name}.zip"))


@router.get(
    "/toolsets/index", response_model=catalog_reads.CatalogIndex, openapi_extra=platform_operation("agents", "read")
)
async def index_toolsets(
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
    query: reads.IndexQuery = None,
    limit: PageLimit = 10,
    cursor: str | None = None,
) -> catalog_reads.CatalogIndex:
    """Search full names and descriptions; definitions are read separately by exact name."""
    values = await list_toolsets(workspace_id, actor, runtime)
    return read_views.toolset_index(values, workspace_id=workspace_id, query=query, limit=limit, cursor=cursor)


@router.get(
    "/toolsets/definitions",
    response_model=reads.Definitions | reads.TextRead,
    openapi_extra=platform_operation("agents", "read"),
)
async def define_toolsets(
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
    name: reads.Names,
    offset: Annotated[int | None, Query(ge=0)] = None,
    limit: reads.TextLimit = 4000,
    version: reads.Version = None,
    query: reads.Search = None,
) -> reads.Definitions | reads.TextRead:
    """Read up to five exact definitions. Oversized values provide a versioned fragment reference."""
    values = await list_toolsets(workspace_id, actor, runtime)
    return reads.definitions(
        {value.key: value for value in values.items},
        name,
        source="/toolsets/definitions",
        offset=offset,
        limit=limit,
        version=version,
        query=query,
    )


@router.get(
    "/agents/{agent_id}/revisions/{revision_id}/compare",
    response_model=reads.TextRead,
    openapi_extra=platform_operation("agents", "read"),
)
async def compare_agent_revisions(
    workspace_id: WorkspaceId,
    agent_id: str,
    revision_id: str,
    base_revision_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    section: read_views.ConfigSection = "all",
    offset: reads.Offset = 0,
    limit: reads.TextLimit = 4000,
    version: reads.Version = None,
    query: reads.Search = None,
) -> reads.TextRead:
    """Versioned unified diff of two revisions of this agent, optionally limited to one configuration section."""
    base = await service.get_revision(runtime.storage, actor, workspace_id, agent_id, base_revision_id)
    target = await service.get_revision(runtime.storage, actor, workspace_id, agent_id, revision_id)
    return reads.read_value(
        read_views.config_diff(base.config, target.config, section),
        source=f"agents/{agent_id}/compare/{base_revision_id}/{revision_id}/{section}",
        offset=offset,
        limit=limit,
        version=version,
        query=query,
    )
