"""Memory Space configuration. File management uses the shared Memory file API."""

from typing import Annotated

from fastapi import APIRouter, Query, Response

from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.resources.memories import spaces
from a13n_service.resources.memories.schemas import MemorySpace, MemorySpaceCreate, MemorySpacePage, MemorySpaceUpdate
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1/memory-spaces", tags=["memory spaces"])


@router.post("", response_model=MemorySpace, status_code=201, openapi_extra={"x-a13n-mcp": True})
async def create_space(
    response: Response, workspace_id: WorkspaceId, body: MemorySpaceCreate, actor: Actor, runtime: CurrentRuntime
) -> MemorySpace:
    return tagged(
        response,
        await spaces.create_space(
            runtime.storage,
            actor,
            workspace_id,
            body,
            settings=runtime.settings.memory,
        ),
    )


@router.get("", response_model=MemorySpacePage, openapi_extra={"x-a13n-mcp": True})
async def list_spaces(
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
    label: Annotated[list[str] | None, Query()] = None,
    enabled: bool | None = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> MemorySpacePage:
    return await spaces.list_spaces(
        runtime.storage,
        actor,
        workspace_id,
        labels=label or [],
        enabled=enabled,
        limit=limit,
        cursor=cursor,
    )


@router.get("/{space_id}", response_model=MemorySpace, openapi_extra={"x-a13n-mcp": True})
async def get_space(
    response: Response,
    workspace_id: WorkspaceId,
    space_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
) -> MemorySpace:
    return tagged(response, await spaces.get_space(runtime.storage, actor, workspace_id, space_id))


@router.patch("/{space_id}", response_model=MemorySpace, openapi_extra={"x-a13n-mcp": True})
async def update_space(
    response: Response,
    workspace_id: WorkspaceId,
    space_id: str,
    body: MemorySpaceUpdate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> MemorySpace:
    return tagged(
        response,
        await spaces.update_space(
            runtime.storage,
            actor,
            workspace_id,
            space_id,
            body,
            if_match=if_match,
            settings=runtime.settings.memory,
        ),
    )
