"""Human-facing monitoring configuration, intentionally excluded from Agent tools and MCP."""

from fastapi import APIRouter, Response

from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.providers.metrics import QueryRequest, QueryResult
from a13n_service.resources.metrics_data_sources import service
from a13n_service.resources.metrics_data_sources.schemas import (
    Capabilities,
    DataSource,
    DataSourceCreate,
    DataSourcePage,
    DataSourceUpdate,
    DeploymentPreset,
)
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1/metrics-data-sources", tags=["metrics-data-sources"])


@router.get("", response_model=DataSourcePage, openapi_extra={"x-a13n-mcp": False})
async def list_metrics_data_sources(
    workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime, limit: PageLimit = 50, cursor: str | None = None
) -> DataSourcePage:
    return await service.list_sources(runtime.storage, actor, workspace_id, limit=limit, cursor=cursor)


@router.post("", response_model=DataSource, status_code=201, openapi_extra={"x-a13n-mcp": False})
async def create_metrics_data_source(
    response: Response, workspace_id: WorkspaceId, body: DataSourceCreate, actor: Actor, runtime: CurrentRuntime
) -> DataSource:
    return tagged(response, await service.create_source(runtime, actor, workspace_id, body))


@router.get("/capabilities", response_model=Capabilities, openapi_extra={"x-a13n-mcp": False})
async def get_metrics_capabilities(workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime) -> Capabilities:
    return await service.capabilities(runtime, actor, workspace_id)


@router.get("/deployment-preset", response_model=DeploymentPreset | None, openapi_extra={"x-a13n-mcp": False})
async def get_metrics_deployment_preset(
    workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime
) -> DeploymentPreset | None:
    return await service.deployment_preset(runtime, actor, workspace_id)


@router.get("/{source_id}", response_model=DataSource, openapi_extra={"x-a13n-mcp": False})
async def get_metrics_data_source(
    response: Response, workspace_id: WorkspaceId, source_id: str, actor: Actor, runtime: CurrentRuntime
) -> DataSource:
    return tagged(response, await service.get_source(runtime.storage, actor, workspace_id, source_id))


@router.patch("/{source_id}", response_model=DataSource, openapi_extra={"x-a13n-mcp": False})
async def update_metrics_data_source(
    response: Response,
    workspace_id: WorkspaceId,
    source_id: str,
    body: DataSourceUpdate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> DataSource:
    return tagged(
        response, await service.update_source(runtime, actor, workspace_id, source_id, body, if_match=if_match)
    )


@router.post("/{source_id}/query", response_model=QueryResult, openapi_extra={"x-a13n-mcp": False})
async def query_metrics_data_source(
    workspace_id: WorkspaceId, source_id: str, body: QueryRequest, actor: Actor, runtime: CurrentRuntime
) -> QueryResult:
    return await service.query_source(runtime, actor, workspace_id, source_id, body)


@router.delete("/{source_id}", status_code=204, openapi_extra={"x-a13n-mcp": False})
async def delete_metrics_data_source(
    workspace_id: WorkspaceId,
    source_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Response:
    await service.delete_source(runtime, actor, workspace_id, source_id, if_match=if_match)
    return Response(status_code=204)
