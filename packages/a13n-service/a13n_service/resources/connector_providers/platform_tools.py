"""Explicit connector application and action discovery for internal Agents."""

from typing import Annotated

from pydantic import Field as Query

from a13n_service.infra import reads
from a13n_service.infra.http import PageLimit
from a13n_service.platform_tools import ToolContext as PlatformContext
from a13n_service.platform_tools import definition
from a13n_service.resources import catalog_reads
from a13n_service.resources.connector_providers import catalog
from a13n_service.resources.connector_providers.schemas import ConnectorActionPage, ConnectorApp, ConnectorAppPage
from a13n_service.resources.runtime import Runtime

type ToolContext = PlatformContext[Runtime]


async def list_connector_apps(
    ctx: ToolContext,
    *,
    provider_id: str,
    query: Annotated[str | None, Query(max_length=128)] = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
    refresh: bool = False,
) -> ConnectorAppPage:
    runtime = ctx.runtime
    actor = ctx.actor
    workspace_id = ctx.workspace_id
    return await catalog.list_apps(
        runtime.storage,
        actor,
        workspace_id,
        provider_id,
        query=query,
        limit=limit,
        cursor=cursor,
        refresh=refresh,
        redis=runtime.redis,
        keys=runtime.keys,
        registry=runtime.registry,
        policy=runtime.endpoint_policy,
        settings=runtime.settings.providers,
    )


async def get_connector_app(ctx: ToolContext, *, provider_id: str, app: str) -> ConnectorApp:
    runtime = ctx.runtime
    actor = ctx.actor
    workspace_id = ctx.workspace_id
    return await catalog.get_app(
        runtime.storage,
        actor,
        workspace_id,
        provider_id,
        app,
        keys=runtime.keys,
        registry=runtime.registry,
        policy=runtime.endpoint_policy,
        settings=runtime.settings.providers,
    )


async def list_connector_actions(ctx: ToolContext, *, provider_id: str, app: str) -> ConnectorActionPage:
    runtime = ctx.runtime
    actor = ctx.actor
    workspace_id = ctx.workspace_id
    return await catalog.list_actions(
        runtime.storage,
        actor,
        workspace_id,
        provider_id,
        app,
        redis=runtime.redis,
        keys=runtime.keys,
        registry=runtime.registry,
        policy=runtime.endpoint_policy,
        settings=runtime.settings.providers,
    )


async def search_connector_actions(
    ctx: ToolContext,
    *,
    provider_id: str,
    app: str,
    query: reads.IndexQuery = None,
    limit: PageLimit = 10,
    cursor: str | None = None,
) -> catalog_reads.CatalogIndex:
    "Search full names and descriptions; definitions are read separately by exact name."
    values = await list_connector_actions(ctx, provider_id=provider_id, app=app)
    return catalog_reads.index(
        values.items,
        scope=f"/connector-providers/{provider_id}/apps/{app}/actions",
        query=query,
        limit=limit,
        cursor=cursor,
    )


async def get_connector_action_definition(
    ctx: ToolContext,
    *,
    provider_id: str,
    app: str,
    name: reads.Names,
    offset: Annotated[int | None, Query(ge=0)] = None,
    limit: reads.TextLimit = 4000,
    version: reads.Version = None,
    query: reads.Search = None,
) -> reads.Definitions | reads.TextRead:
    "Read up to five exact definitions. Oversized values provide a versioned fragment reference."
    values = await list_connector_actions(ctx, provider_id=provider_id, app=app)
    return reads.definitions(
        {value.name: value for value in values.items},
        name,
        source=f"/connector-providers/{provider_id}/apps/{app}/actions/definitions",
        offset=offset,
        limit=limit,
        version=version,
        query=query,
    )


TOOLS = (
    definition(list_connector_apps, group="connections", effect="read"),
    definition(get_connector_app, group="connections", effect="read"),
    definition(list_connector_actions, group="connections", effect="read"),
    definition(search_connector_actions, group="connections", effect="read"),
    definition(get_connector_action_definition, group="connections", effect="read"),
)
