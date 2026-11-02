"""Explicit internal business operations; HTTP admission does not expand this surface."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic import Field as Query
from pydantic.experimental.missing_sentinel import MISSING

from a13n_service.business_tools import ToolContext as BusinessContext
from a13n_service.business_tools import definition
from a13n_service.infra import reads
from a13n_service.infra.http import PageLimit
from a13n_service.infra.labels import Labels
from a13n_service.resources import catalog_reads
from a13n_service.resources.agents import read_views, service
from a13n_service.resources.agents.schemas import (
    Agent,
    AgentConfig,
    AgentConfigurationChanges,
    AgentCreate,
    AgentDescription,
    AgentDuplicate,
    AgentName,
    AgentPage,
    AgentRevision,
    AgentRevisionPage,
    AgentSource,
    AgentUpdate,
    AgentValidate,
)
from a13n_service.resources.agents.tables import PresetKind
from a13n_service.resources.agents.toolsets import ToolsetCatalog
from a13n_service.resources.revisions import Search
from a13n_service.resources.runtime import Runtime

type ToolContext = BusinessContext[Runtime]


class AgentMetadata(BaseModel):
    """Omitted metadata is retained; empty description/labels explicitly clear it."""

    model_config = ConfigDict(extra="forbid")
    name: AgentName | MISSING = MISSING
    description: AgentDescription | MISSING = MISSING
    labels: Labels | MISSING = MISSING


class AgentCreation(AgentConfig):
    name: AgentName
    description: AgentDescription = ""
    labels: Labels = Field(default_factory=dict)


async def create_agent(ctx: ToolContext, *, body: AgentCreation) -> Agent:
    """Create an Agent and its complete initial configuration atomically.

    Model and instructions are direct fields; advanced capabilities are optional.
    """
    config = AgentConfig.model_validate(body.model_dump(exclude={"name", "description", "labels"}))
    return await service.create_agent(
        ctx.runtime.storage,
        ctx.actor,
        ctx.workspace_id,
        AgentCreate(name=body.name, description=body.description, labels=body.labels, config=config),
        registry=ctx.runtime.registry,
        plugins=ctx.runtime.plugins,
    )


async def validate_agent_configuration(ctx: ToolContext, *, body: AgentValidate) -> None:
    """No content when creating a revision of the configuration would accept it, else the same `invalid_argument`
    error with the field's path relative to `config`; nothing is stored."""
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    runtime = ctx.runtime
    await service.validate_revision(
        runtime.storage, actor, workspace_id, body, registry=runtime.registry, plugins=runtime.plugins
    )


async def list_agents(
    ctx: ToolContext,
    *,
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
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    runtime = ctx.runtime
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


async def get_agent(ctx: ToolContext, *, agent_id: str) -> Agent:
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    runtime = ctx.runtime
    return await service.get_agent(runtime.storage, actor, workspace_id, agent_id)


async def update_agent_metadata(
    ctx: ToolContext, *, agent_id: str, body: AgentMetadata, expected_version: str
) -> Agent:
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    runtime = ctx.runtime
    updated = await service.update_agent(
        runtime.storage,
        actor,
        workspace_id,
        agent_id,
        AgentUpdate.model_validate(body.model_dump(exclude_unset=True)),
        if_match=expected_version,
    )
    return updated


async def archive_agent(ctx: ToolContext, *, agent_id: str, expected_version: str) -> Agent:
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    runtime = ctx.runtime
    archived = await service.set_archived(
        runtime.storage, actor, workspace_id, agent_id, archived=True, if_match=expected_version
    )
    return archived


async def unarchive_agent(ctx: ToolContext, *, agent_id: str, expected_version: str) -> Agent:
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    runtime = ctx.runtime
    restored = await service.set_archived(
        runtime.storage, actor, workspace_id, agent_id, archived=False, if_match=expected_version
    )
    return restored


async def duplicate_agent(ctx: ToolContext, *, agent_id: str, body: AgentDuplicate) -> Agent:
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    runtime = ctx.runtime
    created = await service.duplicate_agent(
        runtime.storage, actor, workspace_id, agent_id, body, registry=runtime.registry, plugins=runtime.plugins
    )
    return created


async def update_agent_configuration(
    ctx: ToolContext,
    *,
    agent_id: str,
    expected_version: str,
    body: AgentConfigurationChanges,
    base_revision_id: str | None = None,
    make_default: bool = True,
    note: Annotated[str | None, Field(max_length=2048)] = None,
) -> service.ConfigurationChange:
    """Publish only specified configuration changes against the version read.

    Omitted fields preserve the base revision, including exact Skill pins. Supplied
    collections and structured sections replace those sections whole. Null only
    clears nullable fields. Related changes validate and publish atomically.
    The default revision is the base unless a historical base is explicitly named.
    """
    return await service.change_configuration(
        ctx.runtime.storage,
        ctx.actor,
        ctx.workspace_id,
        agent_id,
        body,
        expected_version=expected_version,
        registry=ctx.runtime.registry,
        plugins=ctx.runtime.plugins,
        base_revision_id=base_revision_id,
        make_default=make_default,
        note=note,
    )


async def list_agent_revisions(
    ctx: ToolContext,
    *,
    agent_id: str,
    view: reads.View = "compact",
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    cursor: str | None = None,
) -> AgentRevisionPage | read_views.RevisionSummaryPage:
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    runtime = ctx.runtime
    if view == "compact":
        return await service.list_revision_summaries(
            runtime.storage, actor, workspace_id, agent_id, limit=reads.page_limit(view, limit), cursor=cursor
        )
    return await service.list_revisions(
        runtime.storage, actor, workspace_id, agent_id, limit=reads.page_limit(view, limit), cursor=cursor
    )


async def get_agent_revision(
    ctx: ToolContext,
    *,
    agent_id: str,
    revision_id: str,
    view: reads.View = "compact",
    section: read_views.ConfigSection | None = None,
    offset: reads.Offset = 0,
    limit: reads.TextLimit = 4000,
    version: reads.Version = None,
    query: reads.Search = None,
) -> AgentRevision | read_views.RevisionSummary | reads.TextRead:
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    runtime = ctx.runtime
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


async def set_default_agent_revision(
    ctx: ToolContext, *, agent_id: str, revision_id: str, expected_version: str
) -> Agent:
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    runtime = ctx.runtime
    updated = await service.set_default(
        runtime.storage,
        actor,
        workspace_id,
        agent_id,
        revision_id,
        if_match=expected_version,
        registry=runtime.registry,
        plugins=runtime.plugins,
    )
    return updated


async def list_toolsets(ctx: ToolContext) -> ToolsetCatalog:
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    runtime = ctx.runtime
    return await service.toolset_catalog(runtime.storage, actor, workspace_id, registry=runtime.registry)


async def search_toolsets(
    ctx: ToolContext, *, query: reads.IndexQuery = None, limit: PageLimit = 10, cursor: str | None = None
) -> catalog_reads.CatalogIndex:
    """Search full names and descriptions; definitions are read separately by exact name."""
    workspace_id = ctx.workspace_id
    values = await list_toolsets(ctx)
    return read_views.toolset_index(values, workspace_id=workspace_id, query=query, limit=limit, cursor=cursor)


async def get_toolset_definition(
    ctx: ToolContext,
    *,
    name: reads.Names,
    offset: Annotated[int | None, Query(ge=0)] = None,
    limit: reads.TextLimit = 4000,
    version: reads.Version = None,
    query: reads.Search = None,
) -> reads.Definitions | reads.TextRead:
    """Read up to five exact definitions. Oversized values provide a versioned fragment reference."""
    values = await list_toolsets(ctx)
    return reads.definitions(
        {value.key: value for value in values.items},
        name,
        source="/toolsets/definitions",
        offset=offset,
        limit=limit,
        version=version,
        query=query,
    )


async def compare_agent_revisions(
    ctx: ToolContext,
    *,
    agent_id: str,
    revision_id: str,
    base_revision_id: str,
    section: read_views.ConfigSection = "all",
    offset: reads.Offset = 0,
    limit: reads.TextLimit = 4000,
    version: reads.Version = None,
    query: reads.Search = None,
) -> reads.TextRead:
    """Versioned unified diff of two revisions of this agent, optionally limited to one configuration section."""
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    runtime = ctx.runtime
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


TOOLS = (
    definition(create_agent, group="agents", effect="write"),
    definition(validate_agent_configuration, group="agents", effect="read"),
    definition(list_agents, group="agents", effect="read"),
    definition(get_agent, group="agents", effect="read"),
    definition(update_agent_metadata, group="agents", effect="write"),
    definition(archive_agent, group="agents", effect="write"),
    definition(unarchive_agent, group="agents", effect="write"),
    definition(duplicate_agent, group="agents", effect="write"),
    definition(update_agent_configuration, group="agents", effect="write"),
    definition(list_agent_revisions, group="agents", effect="read"),
    definition(get_agent_revision, group="agents", effect="read"),
    definition(set_default_agent_revision, group="agents", effect="write"),
    definition(list_toolsets, group="agents", effect="read"),
    definition(search_toolsets, group="agents", effect="read"),
    definition(get_toolset_definition, group="agents", effect="read"),
    definition(compare_agent_revisions, group="agents", effect="read"),
)
