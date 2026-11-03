"""Computer control and viewer access, independent of Run continuation."""

from fastapi import APIRouter, Response

from a13n_service.infra.http import IfMatch, tagged
from a13n_service.runs.environments.desktop import control, viewer
from a13n_service.runs.environments.desktop.schemas import Control, OpenViewer, Renew, Takeover, Viewer
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1/environments/{environment_id}", tags=["environments"])


# Live execution/control and viewer credentials are outside the MCP management surface.
@router.get("/desktop-control", response_model=Control, openapi_extra={"x-a13n-mcp": False})
async def get_desktop_control(
    runtime: CurrentRuntime, response: Response, workspace_id: WorkspaceId, environment_id: str, actor: Actor
) -> Control:
    response.headers["Cache-Control"] = "no-store"
    return tagged(response, await control.get(runtime, actor, workspace_id, environment_id))


@router.post("/desktop-control/takeover", response_model=Control, openapi_extra={"x-a13n-mcp": False})
async def take_over_desktop(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    environment_id: str,
    body: Takeover,
    actor: Actor,
    if_match: IfMatch = None,
) -> Control:
    response.headers["Cache-Control"] = "no-store"
    return tagged(
        response, await control.takeover(runtime, actor, workspace_id, environment_id, body, if_match=if_match)
    )


@router.post("/desktop-control/renew", response_model=Control, openapi_extra={"x-a13n-mcp": False})
async def renew_desktop_control(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    environment_id: str,
    body: Renew,
    actor: Actor,
) -> Control:
    response.headers["Cache-Control"] = "no-store"
    return tagged(response, await control.renew(runtime, actor, workspace_id, environment_id, body))


@router.post("/desktop-control/handback", response_model=Control, openapi_extra={"x-a13n-mcp": False})
async def return_desktop_control(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    environment_id: str,
    actor: Actor,
    if_match: IfMatch = None,
) -> Control:
    response.headers["Cache-Control"] = "no-store"
    return tagged(response, await control.handback(runtime, actor, workspace_id, environment_id, if_match=if_match))


@router.post("/desktop-viewer", response_model=Viewer, openapi_extra={"x-a13n-mcp": False})
async def open_desktop_viewer(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    environment_id: str,
    body: OpenViewer,
    actor: Actor,
) -> Viewer:
    response.headers["Cache-Control"] = "no-store"
    return await viewer.open_viewer(runtime, actor, workspace_id, environment_id, body)
