"""Environment instances of a workspace, and a thread's desired mounts under the thread's `If-Match`."""

from typing import Annotated

from fastapi import APIRouter, Query, Response

from a13n_service.infra.http import IfMatch, PageLimit, etag, tagged
from a13n_service.providers.metrics import QueryResult
from a13n_service.runs.environments import desktop_preview, e2b_creation, mounts, service
from a13n_service.runs.environments.monitoring.bindings import Selection
from a13n_service.runs.environments.monitoring.proxy import LocalResult
from a13n_service.runs.environments.monitoring.query import (
    CollectorStatus,
    EnvironmentQuery,
    collector_status,
    query_environment,
)
from a13n_service.runs.environments.schemas import (
    ComputerCheck,
    EnvironmentPage,
    EnvironmentUpdate,
    EnvironmentView,
    ExternalTargetCreate,
    ManagedEnvironmentCreate,
    MountCreate,
    MountPage,
    MountUpdate,
    MountView,
)
from a13n_service.runs.environments.tables import STATUSES
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1", tags=["environments"])


@router.get("/environments", response_model=EnvironmentPage, openapi_extra={"x-a13n-mcp": True})
async def list_environments(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    actor: Actor,
    status: Annotated[str | None, Query(pattern=f"^({'|'.join(STATUSES)})$")] = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> EnvironmentPage:
    return await service.list_environments(
        runtime.storage, actor, workspace_id, status=status, limit=limit, cursor=cursor
    )


@router.post("/environments", response_model=EnvironmentView, status_code=201, openapi_extra={"x-a13n-mcp": True})
async def create_environment(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    body: ManagedEnvironmentCreate | ExternalTargetCreate,
    actor: Actor,
) -> EnvironmentView:
    """Reserve a managed sandbox from a template (`creating`), or register an external envd target (`ready`)."""
    match body:
        case ManagedEnvironmentCreate():
            result = await service.reserve_environment(runtime, actor, workspace_id, body)
        case ExternalTargetCreate():
            result = await service.register_external(runtime, actor, workspace_id, body)
    return tagged(response, result)


@router.get("/environments/{environment_id}", response_model=EnvironmentView, openapi_extra={"x-a13n-mcp": True})
async def get_environment(
    runtime: CurrentRuntime, response: Response, workspace_id: WorkspaceId, environment_id: str, actor: Actor
) -> EnvironmentView:
    result = await service.get_environment(runtime.storage, actor, workspace_id, environment_id)
    return tagged(response, result)


@router.patch("/environments/{environment_id}", response_model=EnvironmentView, openapi_extra={"x-a13n-mcp": True})
async def update_environment(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    environment_id: str,
    body: EnvironmentUpdate,
    actor: Actor,
    if_match: IfMatch = None,
) -> EnvironmentView:
    result = await service.update_environment(runtime, actor, workspace_id, environment_id, body, if_match=if_match)
    return tagged(response, result)


@router.post(
    "/environments/{environment_id}/metrics/query",
    response_model=QueryResult | LocalResult,
    openapi_extra={"x-a13n-mcp": False},
)
async def query_environment_metrics(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, environment_id: str, body: EnvironmentQuery, actor: Actor
) -> QueryResult | LocalResult:
    return await query_environment(runtime, actor, workspace_id, environment_id, body)


@router.get(
    "/environments/{environment_id}/metrics/collector",
    response_model=CollectorStatus,
    openapi_extra={"x-a13n-mcp": False},
)
async def get_collector_status(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, environment_id: str, actor: Actor
) -> CollectorStatus:
    return await collector_status(runtime, actor, workspace_id, environment_id)


@router.put(
    "/environments/{environment_id}/metrics-binding",
    response_model=EnvironmentView,
    openapi_extra={"x-a13n-mcp": False},
)
async def select_metrics_source(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    environment_id: str,
    body: Selection,
    actor: Actor,
    if_match: IfMatch = None,
) -> EnvironmentView:
    return tagged(
        response,
        await service.select_metrics_source(
            runtime,
            actor,
            workspace_id,
            environment_id,
            body,
            if_match=if_match,
        ),
    )


@router.post(
    "/environments/{environment_id}/stop",
    response_model=EnvironmentView,
    status_code=202,
    openapi_extra={"x-a13n-mcp": True},
)
async def stop_environment(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    environment_id: str,
    actor: Actor,
    if_match: IfMatch = None,
) -> EnvironmentView:
    result = await service.stop_environment(runtime, actor, workspace_id, environment_id, if_match=if_match)
    return tagged(response, result)


@router.delete(
    "/environments/{environment_id}",
    response_model=EnvironmentView,
    status_code=202,
    openapi_extra={"x-a13n-mcp": True},
)
async def delete_environment(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    environment_id: str,
    actor: Actor,
    if_match: IfMatch = None,
) -> EnvironmentView:
    result = await service.delete_environment(runtime, actor, workspace_id, environment_id, if_match=if_match)
    return tagged(response, result)


@router.get("/threads/{thread_id}/environments", response_model=MountPage, openapi_extra={"x-a13n-mcp": True})
async def list_environment_mounts(
    runtime: CurrentRuntime, response: Response, workspace_id: WorkspaceId, thread_id: str, actor: Actor
) -> MountPage:
    page, version = await mounts.list_mounts(runtime.storage, actor, workspace_id, thread_id)
    response.headers["ETag"] = etag(thread_id, version)
    return page


@router.post(
    "/threads/{thread_id}/environments", response_model=MountView, status_code=201, openapi_extra={"x-a13n-mcp": True}
)
async def add_environment_mount(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    thread_id: str,
    body: MountCreate,
    actor: Actor,
    if_match: IfMatch = None,
) -> MountView:
    mount, version = await mounts.add_mount(runtime.storage, actor, workspace_id, thread_id, body, if_match=if_match)
    response.headers["ETag"] = etag(thread_id, version)
    return mount


@router.delete("/threads/{thread_id}/environments/{name}", status_code=204, openapi_extra={"x-a13n-mcp": True})
async def remove_environment_mount(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    thread_id: str,
    name: str,
    actor: Actor,
    if_match: IfMatch = None,
) -> Response:
    version = await mounts.remove_mount(runtime.storage, actor, workspace_id, thread_id, name, if_match=if_match)
    return Response(status_code=204, headers={"ETag": etag(thread_id, version)})


@router.patch("/threads/{thread_id}/environments/{name}", response_model=MountView, openapi_extra={"x-a13n-mcp": True})
async def update_environment_mount(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: WorkspaceId,
    thread_id: str,
    name: str,
    body: MountUpdate,
    actor: Actor,
    if_match: IfMatch = None,
) -> MountView:
    mount, version = await mounts.update_mount(
        runtime.storage, actor, workspace_id, thread_id, name, body, if_match=if_match
    )
    response.headers["ETag"] = etag(thread_id, version)
    return mount


@router.post(
    "/environments/{environment_id}/computer/check",
    response_model=ComputerCheck,
    openapi_extra={"x-a13n-mcp": True},
)
async def check_computer(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, environment_id: str, actor: Actor
) -> ComputerCheck:
    """Probe desktop readiness without capturing the screen, sending input or starting the instance."""
    return await service.check_computer(runtime, actor, workspace_id, environment_id)


@router.get(
    "/environments/{environment_id}/creation",
    response_model=e2b_creation.CreationProgress | None,
    openapi_extra={"x-a13n-mcp": True},
)
async def get_environment_creation(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, environment_id: str, actor: Actor, response: Response
) -> e2b_creation.CreationProgress | None:
    response.headers["Cache-Control"] = "no-store"
    return await e2b_creation.progress(runtime, actor, workspace_id, environment_id)


@router.post(
    "/environments/{environment_id}/retry-creation",
    response_model=EnvironmentView,
    status_code=202,
    openapi_extra={"x-a13n-mcp": True},
)
async def retry_environment_creation(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    environment_id: str,
    actor: Actor,
    response: Response,
    if_match: IfMatch = None,
) -> EnvironmentView:
    return tagged(response, await e2b_creation.retry(runtime, actor, workspace_id, environment_id, if_match=if_match))


@router.get(
    "/environments/{environment_id}/desktop-preview",
    response_class=Response,
    openapi_extra={"x-a13n-mcp": False},
    responses={200: {"content": {"image/png": {}, "image/jpeg": {}}}},
)
async def get_environment_desktop_preview(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, environment_id: str, actor: Actor
) -> Response:
    screenshot = await desktop_preview.capture(runtime, actor, workspace_id, environment_id)
    return Response(
        content=screenshot.data,
        media_type=screenshot.observation.mime_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
