"""Native Workspace memory routes. No provider credentials or filters cross this boundary."""

from typing import Annotated

from a13n_harness.memory import MemoryScope as ScopeKind
from fastapi import APIRouter, Depends, Path, Query, Request, Response

from a13n_service.application_errors import ErrorCategory
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.iam.http.resource_dependencies import WorkspaceId
from a13n_service.interactions.domain import ThreadId
from a13n_service.request_runtime import get_process_runtime

from .domain import Memory, MemoryAccess, MemoryCollection, MemoryScope, MemorySearch, MemoryWrite
from .service import MemoryService, failure

router = APIRouter(prefix="/api/v1/workspaces/{workspace}/memory-providers/{provider_id}", tags=["memory"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]


def memory_scope(scope: ScopeKind, subject_id: Annotated[ThreadId | None, Query()] = None) -> MemoryScope:
    try:
        return MemoryScope(scope=scope, subject_id=subject_id)
    except ValueError as error:
        raise failure(
            "invalid_memory_scope",
            "Select a thread or agent subject, or the authenticated user scope.",
            ErrorCategory.invalid_request,
        ) from error


Scope = Annotated[MemoryScope, Depends(memory_scope)]
MemoryId = Annotated[str, Path(min_length=1, max_length=512)]


def _memory(request: Request) -> MemoryService:
    runtime = get_process_runtime(request)
    if runtime is None or runtime.shared.memories is None:
        raise failure("memory_unavailable", "Memory is unavailable.", ErrorCategory.unavailable)
    return runtime.shared.memories


@router.get("/memory-access", response_model=MemoryAccess)
async def get_memory_access(
    request: Request, actor: Actor, workspace_id: WorkspaceId, provider_id: str, scope: Scope
) -> MemoryAccess:
    return await _memory(request).access(
        actor=actor, workspace_id=workspace_id, provider_id=provider_id, selection=scope
    )


@router.get("/memories", response_model=MemoryCollection)
async def list_memories(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    provider_id: str,
    scope: Scope,
    limit: Annotated[int, Query(ge=1, le=1000, description="Maximum loaded records, not a total count.")] = 1000,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> MemoryCollection:
    return await _memory(request).list(
        actor=actor, workspace_id=workspace_id, provider_id=provider_id, selection=scope, limit=limit, cursor=cursor
    )


@router.post("/memories/search", response_model=MemoryCollection)
async def search_memories(
    request: Request, actor: Actor, workspace_id: WorkspaceId, provider_id: str, scope: Scope, body: MemorySearch
) -> MemoryCollection:
    return await _memory(request).search(
        actor=actor, workspace_id=workspace_id, provider_id=provider_id, selection=scope, query=body
    )


@router.post("/memories", response_model=Memory, status_code=201)
async def add_memory(
    request: Request, actor: Actor, workspace_id: WorkspaceId, provider_id: str, scope: Scope, body: MemoryWrite
) -> Memory:
    return await _memory(request).add(
        actor=actor, workspace_id=workspace_id, provider_id=provider_id, selection=scope, text=body.text
    )


@router.get("/memories/{memory_id}", response_model=Memory)
async def get_memory(
    request: Request, actor: Actor, workspace_id: WorkspaceId, provider_id: str, scope: Scope, memory_id: MemoryId
) -> Memory:
    return await _memory(request).get(
        actor=actor, workspace_id=workspace_id, provider_id=provider_id, selection=scope, memory_id=memory_id
    )


@router.put("/memories/{memory_id}", response_model=Memory)
async def update_memory(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    provider_id: str,
    scope: Scope,
    memory_id: MemoryId,
    body: MemoryWrite,
) -> Memory:
    return await _memory(request).update(
        actor=actor,
        workspace_id=workspace_id,
        provider_id=provider_id,
        selection=scope,
        memory_id=memory_id,
        text=body.text,
    )


@router.delete("/memories/{memory_id}", status_code=204)
async def delete_memory(
    request: Request, actor: Actor, workspace_id: WorkspaceId, provider_id: str, scope: Scope, memory_id: MemoryId
) -> Response:
    await _memory(request).delete(
        actor=actor, workspace_id=workspace_id, provider_id=provider_id, selection=scope, memory_id=memory_id
    )
    return Response(status_code=204)
