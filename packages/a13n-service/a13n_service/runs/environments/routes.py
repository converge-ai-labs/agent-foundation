"""Environment instances of a workspace, and a thread's desired mounts under the thread's `If-Match`."""

from typing import Annotated

from fastapi import APIRouter, Query, Response

from a13n_service.infra.http import IfMatch, PageLimit, etag, tagged
from a13n_service.runs.environments import mounts, service
from a13n_service.runs.environments.schemas import (
    EnvironmentPage,
    EnvironmentUpdate,
    EnvironmentView,
    ExternalTargetCreate,
    ManagedEnvironmentCreate,
    MountCreate,
    MountPage,
    MountView,
)
from a13n_service.runs.environments.tables import STATUSES
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1", tags=["environments"])


@router.get("/environments", response_model=EnvironmentPage)
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


@router.post("/environments", response_model=EnvironmentView, status_code=201)
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


@router.get("/environments/{environment_id}", response_model=EnvironmentView)
async def get_environment(
    runtime: CurrentRuntime, response: Response, workspace_id: WorkspaceId, environment_id: str, actor: Actor
) -> EnvironmentView:
    result = await service.get_environment(runtime.storage, actor, workspace_id, environment_id)
    return tagged(response, result)


@router.patch("/environments/{environment_id}", response_model=EnvironmentView)
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


@router.post("/environments/{environment_id}/stop", response_model=EnvironmentView, status_code=202)
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


@router.delete("/environments/{environment_id}", response_model=EnvironmentView, status_code=202)
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


@router.get("/threads/{thread_id}/environments", response_model=MountPage)
async def list_mounts(
    runtime: CurrentRuntime, response: Response, workspace_id: WorkspaceId, thread_id: str, actor: Actor
) -> MountPage:
    page, version = await mounts.list_mounts(runtime.storage, actor, workspace_id, thread_id)
    response.headers["ETag"] = etag(thread_id, version)
    return page


@router.post("/threads/{thread_id}/environments", response_model=MountView, status_code=201)
async def add_mount(
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


@router.delete("/threads/{thread_id}/environments/{name}", status_code=204)
async def remove_mount(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    thread_id: str,
    name: str,
    actor: Actor,
    if_match: IfMatch = None,
) -> Response:
    version = await mounts.remove_mount(runtime.storage, actor, workspace_id, thread_id, name, if_match=if_match)
    return Response(status_code=204, headers={"ETag": etag(thread_id, version)})
