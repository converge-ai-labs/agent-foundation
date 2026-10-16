"""Control-plane only: no Service WebSocket/video proxy. Access responses are never cached."""

from fastapi import APIRouter, Response

from a13n_service.runs.desktop_assistance import service, takeover
from a13n_service.runs.desktop_assistance.schemas import Access, Completion, Control, Request, View
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1", tags=["desktop-assistance"])


@router.get("/runs/{run_id}/desktop-assistance/{call_id}", response_model=View, openapi_extra={"x-a13n-mcp": True})
async def get_assistance(
    runtime: CurrentRuntime, actor: Actor, workspace_id: WorkspaceId, run_id: str, call_id: str
) -> View:
    return await service.read(runtime, actor, workspace_id, run_id, call_id)


@router.post("/desktop-assistance/{assistance_id}/view", response_model=Access)
async def view_desktop(
    runtime: CurrentRuntime,
    actor: Actor,
    workspace_id: WorkspaceId,
    assistance_id: str,
    body: Control,
    response: Response,
    renew: bool = False,
) -> Access:
    response.headers["Cache-Control"] = "no-store"
    return await service.access(runtime, actor, workspace_id, assistance_id, body, mode="view", renew=renew)


@router.post("/desktop-assistance/{assistance_id}/control", response_model=Access)
async def control_desktop(
    runtime: CurrentRuntime,
    actor: Actor,
    workspace_id: WorkspaceId,
    assistance_id: str,
    body: Control,
    response: Response,
    renew: bool = False,
) -> Access:
    response.headers["Cache-Control"] = "no-store"
    return await service.access(runtime, actor, workspace_id, assistance_id, body, mode="control", renew=renew)


@router.post("/desktop-assistance/{assistance_id}/complete", response_model=View)
async def complete_assistance(
    runtime: CurrentRuntime, actor: Actor, workspace_id: WorkspaceId, assistance_id: str, body: Completion
) -> View:
    return await service.complete(runtime, actor, workspace_id, assistance_id, body)


@router.post("/desktop-assistance/{assistance_id}/disconnect", response_model=View)
async def disconnect_desktop(
    runtime: CurrentRuntime, actor: Actor, workspace_id: WorkspaceId, assistance_id: str, body: Control
) -> View:
    return await service.disconnect(runtime, actor, workspace_id, assistance_id, body)


@router.get("/runs/{run_id}/desktop-control", response_model=View | None, openapi_extra={"x-a13n-mcp": True})
async def get_control(runtime: CurrentRuntime, actor: Actor, workspace_id: WorkspaceId, run_id: str) -> View | None:
    return await takeover.read(runtime, actor, workspace_id, run_id)


@router.post("/runs/{run_id}/desktop-control", response_model=View)
async def open_control(
    runtime: CurrentRuntime, actor: Actor, workspace_id: WorkspaceId, run_id: str, body: Request
) -> View:
    return await takeover.open_desktop(runtime, actor, workspace_id, run_id, body)


@router.post("/desktop-assistance/{assistance_id}/request-control", response_model=View)
async def request_control(runtime: CurrentRuntime, actor: Actor, workspace_id: WorkspaceId, assistance_id: str) -> View:
    return await takeover.acquire(runtime, actor, workspace_id, assistance_id)
