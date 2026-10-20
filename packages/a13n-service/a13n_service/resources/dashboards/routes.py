"""Human-facing dashboard documents, excluded from Agent tools and MCP."""

from fastapi import APIRouter, Response

from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.resources.dashboards import service
from a13n_service.resources.dashboards.schemas import (
    Dashboard,
    DashboardConfig,
    DashboardCreate,
    DashboardPage,
    DashboardUpdate,
)
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1/metrics-dashboards", tags=["metrics-dashboards"])


@router.get("", response_model=DashboardPage, openapi_extra={"x-a13n-mcp": False})
async def list_dashboards(
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> DashboardPage:
    return await service.list_dashboards(runtime.storage, actor, workspace_id, limit=limit, cursor=cursor)


@router.get("/preset", response_model=DashboardConfig, openapi_extra={"x-a13n-mcp": False})
async def get_dashboard_preset(workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime) -> DashboardConfig:
    return await service.get_preset(runtime.storage, actor, workspace_id)


@router.post("", response_model=Dashboard, status_code=201, openapi_extra={"x-a13n-mcp": False})
async def create_dashboard(
    response: Response,
    workspace_id: WorkspaceId,
    body: DashboardCreate,
    actor: Actor,
    runtime: CurrentRuntime,
) -> Dashboard:
    return tagged(response, await service.create_dashboard(runtime.storage, actor, workspace_id, body))


@router.get("/{dashboard_id}", response_model=Dashboard, openapi_extra={"x-a13n-mcp": False})
async def get_dashboard(
    response: Response,
    workspace_id: WorkspaceId,
    dashboard_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
) -> Dashboard:
    return tagged(response, await service.get_dashboard(runtime.storage, actor, workspace_id, dashboard_id))


@router.patch("/{dashboard_id}", response_model=Dashboard, openapi_extra={"x-a13n-mcp": False})
async def update_dashboard(
    response: Response,
    workspace_id: WorkspaceId,
    dashboard_id: str,
    body: DashboardUpdate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Dashboard:
    return tagged(
        response,
        await service.update_dashboard(
            runtime.storage,
            actor,
            workspace_id,
            dashboard_id,
            body,
            if_match=if_match,
        ),
    )


@router.delete("/{dashboard_id}", status_code=204, openapi_extra={"x-a13n-mcp": False})
async def delete_dashboard(
    workspace_id: WorkspaceId,
    dashboard_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Response:
    await service.delete_dashboard(runtime.storage, actor, workspace_id, dashboard_id, if_match=if_match)
    return Response(status_code=204)
