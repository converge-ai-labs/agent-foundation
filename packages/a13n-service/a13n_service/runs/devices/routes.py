"""Human-facing Device management; excluded from Agent and MCP execution surfaces."""

from fastapi import APIRouter, Response

from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.providers.metrics import QueryResult
from a13n_service.runs.devices import service
from a13n_service.runs.devices.query import query_device
from a13n_service.runs.devices.schemas import Device, DeviceCreate, DeviceCredential, DevicePage, DeviceUpdate
from a13n_service.runs.environments.monitoring.proxy import LocalResult
from a13n_service.runs.environments.monitoring.query import EnvironmentQuery
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1/devices", tags=["monitoring-devices"])


@router.get("", response_model=DevicePage, openapi_extra={"x-a13n-mcp": False})
async def list_devices(
    workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime, limit: PageLimit = 50, cursor: str | None = None
) -> DevicePage:
    return await service.list_devices(runtime, actor, workspace_id, limit=limit, cursor=cursor)


@router.post("", response_model=DeviceCredential, status_code=201, openapi_extra={"x-a13n-mcp": False})
async def create_device(
    response: Response, workspace_id: WorkspaceId, body: DeviceCreate, actor: Actor, runtime: CurrentRuntime
) -> DeviceCredential:
    result = await service.create_device(runtime, actor, workspace_id, body)
    tagged(response, result.device)
    response.headers["cache-control"] = "no-store"
    return result


@router.get("/{device_id}", response_model=Device, openapi_extra={"x-a13n-mcp": False})
async def get_device(
    response: Response, workspace_id: WorkspaceId, device_id: str, actor: Actor, runtime: CurrentRuntime
) -> Device:
    return tagged(response, await service.get_device(runtime, actor, workspace_id, device_id))


@router.patch("/{device_id}", response_model=Device, openapi_extra={"x-a13n-mcp": False})
async def update_device(
    response: Response,
    workspace_id: WorkspaceId,
    device_id: str,
    body: DeviceUpdate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Device:
    return tagged(
        response, await service.update_device(runtime, actor, workspace_id, device_id, body, if_match=if_match)
    )


@router.post("/{device_id}/rotate-credential", response_model=DeviceCredential, openapi_extra={"x-a13n-mcp": False})
async def rotate_device_credential(
    response: Response,
    workspace_id: WorkspaceId,
    device_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> DeviceCredential:
    result = await service.rotate(runtime, actor, workspace_id, device_id, if_match=if_match)
    tagged(response, result.device)
    response.headers["cache-control"] = "no-store"
    return result


@router.delete("/{device_id}", status_code=204, openapi_extra={"x-a13n-mcp": False})
async def delete_device(
    workspace_id: WorkspaceId, device_id: str, actor: Actor, runtime: CurrentRuntime, if_match: IfMatch = None
) -> Response:
    await service.delete_device(runtime, actor, workspace_id, device_id, if_match=if_match)
    return Response(status_code=204)


@router.post(
    "/{device_id}/metrics/query", response_model=QueryResult | LocalResult, openapi_extra={"x-a13n-mcp": False}
)
async def query_device_metrics(
    workspace_id: WorkspaceId, device_id: str, body: EnvironmentQuery, actor: Actor, runtime: CurrentRuntime
) -> QueryResult | LocalResult:
    return await query_device(runtime, actor, workspace_id, device_id, body)
