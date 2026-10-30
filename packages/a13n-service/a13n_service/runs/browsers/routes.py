"""Managed Browser metadata and explicit human control."""

from fastapi import APIRouter, Response

from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.runs.browsers import control, service
from a13n_service.runs.browsers.schemas import (
    Browser,
    BrowserAttach,
    BrowserBinding,
    BrowserCreate,
    BrowserPage,
    RenewControl,
    Takeover,
    Viewer,
)
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1", tags=["browsers"])


@router.post("/browsers", status_code=201, openapi_extra={"x-a13n-mcp": True})
async def create_browser(
    runtime: CurrentRuntime, response: Response, workspace_id: WorkspaceId, actor: Actor, body: BrowserCreate
) -> Browser:
    return tagged(response, await service.create(runtime, actor, workspace_id, body))


@router.post("/browsers/attach", status_code=201, openapi_extra={"x-a13n-mcp": True})
async def attach_browser(
    runtime: CurrentRuntime, response: Response, workspace_id: WorkspaceId, actor: Actor, body: BrowserAttach
) -> Browser:
    return tagged(response, await service.attach(runtime, actor, workspace_id, body))


@router.get("/browsers", openapi_extra={"x-a13n-mcp": True})
async def list_browsers(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, actor: Actor, limit: PageLimit = 50, cursor: str | None = None
) -> BrowserPage:
    return await service.list_browsers(runtime, actor, workspace_id, limit=limit, cursor=cursor)


@router.get("/browsers/{browser_id}", openapi_extra={"x-a13n-mcp": True})
async def get_browser(
    runtime: CurrentRuntime, response: Response, workspace_id: WorkspaceId, actor: Actor, browser_id: str
) -> Browser:
    return tagged(response, await service.get(runtime, actor, workspace_id, browser_id))


@router.post("/browsers/{browser_id}/prepare", openapi_extra={"x-a13n-mcp": True})
async def prepare_browser(
    runtime: CurrentRuntime, response: Response, workspace_id: WorkspaceId, actor: Actor, browser_id: str
) -> Browser:
    return tagged(response, await service.prepare(runtime, actor, workspace_id, browser_id))


@router.post("/browsers/{browser_id}/release", openapi_extra={"x-a13n-mcp": True})
async def release_browser(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    actor: Actor,
    browser_id: str,
    if_match: IfMatch = None,
) -> Browser:
    return tagged(response, await service.release(runtime, actor, workspace_id, browser_id, if_match=if_match))


@router.get("/threads/{thread_id}/browser", openapi_extra={"x-a13n-mcp": True})
async def get_binding(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, actor: Actor, thread_id: str
) -> BrowserBinding | None:
    return await service.binding(runtime, actor, workspace_id, thread_id)


@router.put("/threads/{thread_id}/browser", openapi_extra={"x-a13n-mcp": True})
async def bind_browser(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    actor: Actor,
    thread_id: str,
    body: BrowserBinding | None = None,
    if_match: IfMatch = None,
) -> BrowserBinding | None:
    return await service.bind(runtime, actor, workspace_id, thread_id, body, if_match=if_match)


@router.post("/browsers/{browser_id}/takeover", openapi_extra={"x-a13n-mcp": False})
async def takeover_browser(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    actor: Actor,
    browser_id: str,
    body: Takeover,
    if_match: IfMatch = None,
) -> Browser:
    return tagged(response, await control.takeover(runtime, actor, workspace_id, browser_id, body, if_match=if_match))


@router.post("/browsers/{browser_id}/renew", openapi_extra={"x-a13n-mcp": False})
async def renew_browser_control(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    actor: Actor,
    browser_id: str,
    body: RenewControl,
) -> Browser:
    return tagged(response, await control.renew(runtime, actor, workspace_id, browser_id, body))


@router.post("/browsers/{browser_id}/handback", openapi_extra={"x-a13n-mcp": False})
async def handback_browser(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    actor: Actor,
    browser_id: str,
    if_match: IfMatch = None,
) -> Browser:
    return tagged(response, await control.handback(runtime, actor, workspace_id, browser_id, if_match=if_match))


@router.post("/browsers/{browser_id}/viewer", openapi_extra={"x-a13n-mcp": False})
async def browser_viewer(
    runtime: CurrentRuntime, response: Response, workspace_id: WorkspaceId, actor: Actor, browser_id: str
) -> Viewer:
    response.headers["Cache-Control"] = "no-store"
    return await control.viewer(runtime, actor, workspace_id, browser_id)
