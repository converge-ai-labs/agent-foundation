"""The app catalogue of a connector provider usable in a workspace."""

from typing import Annotated

from fastapi import APIRouter, Query

from a13n_service.infra.http import PageLimit
from a13n_service.resources.connector_providers import catalog
from a13n_service.resources.connector_providers.schemas import ConnectorActionPage, ConnectorApp, ConnectorAppPage
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1/connector-providers/{provider_id}", tags=["connections"])


@router.get("/apps", response_model=ConnectorAppPage)
async def list_apps(
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


@router.get("/apps/{app}", response_model=ConnectorApp)
async def get_app(
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


@router.get("/apps/{app}/actions", response_model=ConnectorActionPage)
async def list_actions(
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
