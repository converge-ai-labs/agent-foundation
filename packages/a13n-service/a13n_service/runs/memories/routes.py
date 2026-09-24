"""Thread memory mounts, edited under the thread ETag like the thread's other mounts."""

from fastapi import APIRouter, Response

from a13n_service.infra.http import IfMatch, etag
from a13n_service.resources.memories.schemas import MemoryMount
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.runs.memories import mounts
from a13n_service.runs.memories.schemas import MemoryMountPage, MemoryMountUpdate
from a13n_service.tenancy.requests import Actor

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/threads/{thread_id}/memories", tags=["memories"])


@router.get("", response_model=MemoryMountPage)
async def list_mounts(
    runtime: CurrentRuntime, response: Response, workspace_id: str, thread_id: str, actor: Actor
) -> MemoryMountPage:
    page, version = await mounts.list_mounts(runtime.storage, actor, workspace_id, thread_id)
    response.headers["ETag"] = etag(thread_id, version)
    return page


@router.post("", response_model=MemoryMount, status_code=201)
async def add_mount(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: str,
    thread_id: str,
    body: MemoryMount,
    actor: Actor,
    if_match: IfMatch = None,
) -> MemoryMount:
    mount, version = await mounts.add_mount(
        runtime.storage,
        actor,
        workspace_id,
        thread_id,
        body,
        if_match=if_match,
        limit=runtime.settings.memory.mounts_per_thread,
    )
    response.headers["ETag"] = etag(thread_id, version)
    return mount


@router.patch("/{name}", response_model=MemoryMount)
async def update_mount(
    runtime: CurrentRuntime,
    response: Response,
    workspace_id: str,
    thread_id: str,
    name: str,
    body: MemoryMountUpdate,
    actor: Actor,
    if_match: IfMatch = None,
) -> MemoryMount:
    mount, version = await mounts.update_mount(
        runtime.storage, actor, workspace_id, thread_id, name, body, if_match=if_match
    )
    response.headers["ETag"] = etag(thread_id, version)
    return mount


@router.delete("/{name}", status_code=204)
async def remove_mount(
    runtime: CurrentRuntime, workspace_id: str, thread_id: str, name: str, actor: Actor, if_match: IfMatch = None
) -> Response:
    version = await mounts.remove_mount(runtime.storage, actor, workspace_id, thread_id, name, if_match=if_match)
    return Response(status_code=204, headers={"ETag": etag(thread_id, version)})
