"""The app catalogue of a connector provider usable in a workspace."""

from typing import Annotated

from fastapi import APIRouter, Query

from a13n_service.infra import reads
from a13n_service.infra.http import PageLimit
from a13n_service.infra.platform import platform_operation
from a13n_service.resources import catalog_reads
from a13n_service.resources.connector_providers import catalog
from a13n_service.resources.connector_providers.schemas import ConnectorActionPage, ConnectorApp, ConnectorAppPage
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1/connector-providers/{provider_id}", tags=["connections"])


@router.get("/apps", response_model=ConnectorAppPage, openapi_extra=platform_operation("connections", "read"))
async def list_connector_apps(
    workspace_id: WorkspaceId,
    provider_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    query: Annotated[str | None, Query(max_length=128)] = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
    refresh: bool = False,
) -> ConnectorAppPage:
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


@router.get("/apps/{app}", response_model=ConnectorApp, openapi_extra=platform_operation("connections", "read"))
async def get_connector_app(
    workspace_id: WorkspaceId, provider_id: str, app: str, actor: Actor, runtime: CurrentRuntime
) -> ConnectorApp:
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


@router.get(
    "/apps/{app}/actions", response_model=ConnectorActionPage, openapi_extra=platform_operation("connections", "read")
)
async def list_connector_actions(
    workspace_id: WorkspaceId, provider_id: str, app: str, actor: Actor, runtime: CurrentRuntime
) -> ConnectorActionPage:
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


@router.get(
    "/apps/{app}/actions/index",
    response_model=catalog_reads.CatalogIndex,
    openapi_extra=platform_operation("connections", "read"),
)
async def index_connector_actions(
    workspace_id: WorkspaceId,
    provider_id: str,
    app: str,
    actor: Actor,
    runtime: CurrentRuntime,
    query: reads.IndexQuery = None,
    limit: PageLimit = 10,
    cursor: str | None = None,
) -> catalog_reads.CatalogIndex:
    """Search full names and descriptions; definitions are read separately by exact name."""
    values = await list_connector_actions(workspace_id, provider_id, app, actor, runtime)
    return catalog_reads.index(values.items, scope=f"/apps/{app}/actions", query=query, limit=limit, cursor=cursor)


@router.get(
    "/apps/{app}/actions/definitions",
    response_model=reads.Definitions | reads.TextRead,
    openapi_extra=platform_operation("connections", "read"),
)
async def define_connector_actions(
    workspace_id: WorkspaceId,
    provider_id: str,
    app: str,
    actor: Actor,
    runtime: CurrentRuntime,
    name: reads.Names,
    offset: Annotated[int | None, Query(ge=0)] = None,
    limit: reads.TextLimit = 4000,
    version: reads.Version = None,
    query: reads.Search = None,
) -> reads.Definitions | reads.TextRead:
    """Read up to five exact definitions. Oversized values provide a versioned fragment reference."""
    values = await list_connector_actions(workspace_id, provider_id, app, actor, runtime)
    return reads.definitions(
        {value.name: value for value in values.items},
        name,
        source=f"/apps/{app}/actions/definitions",
        offset=offset,
        limit=limit,
        version=version,
        query=query,
    )
