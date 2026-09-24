"""Workspace memories: CRUD with ETags, files by path with their own ETags, and file history."""

from typing import Annotated

from fastapi import APIRouter, Query, Response

from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.resources.memories import files, service
from a13n_service.resources.memories.schemas import (
    HistoryPurge,
    Memory,
    MemoryCreate,
    MemoryFile,
    MemoryFileCreate,
    MemoryFileMove,
    MemoryFilePage,
    MemoryFileReplace,
    MemoryFileState,
    MemoryPage,
    MemoryRevisionDetail,
    MemoryRevisionPage,
    MemoryUpdate,
)
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/memories", tags=["memories"])


@router.post("", response_model=Memory, status_code=201)
async def create_memory(
    response: Response, workspace_id: str, body: MemoryCreate, actor: Actor, runtime: CurrentRuntime
) -> Memory:
    result = await service.create_memory(runtime.storage, actor, workspace_id, body, settings=runtime.settings.memory)
    return tagged(response, result)


@router.get("", response_model=MemoryPage)
async def list_memories(
    workspace_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    label: Annotated[list[str] | None, Query()] = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> MemoryPage:
    return await service.list_memories(
        runtime.storage,
        actor,
        workspace_id,
        labels=label or [],
        limit=limit,
        cursor=cursor,
        settings=runtime.settings.memory,
    )


@router.get("/{memory_id}", response_model=Memory)
async def get_memory(
    response: Response, workspace_id: str, memory_id: str, actor: Actor, runtime: CurrentRuntime
) -> Memory:
    result = await service.get_memory(runtime.storage, actor, workspace_id, memory_id, settings=runtime.settings.memory)
    return tagged(response, result)


@router.patch("/{memory_id}", response_model=Memory)
async def update_memory(
    response: Response,
    workspace_id: str,
    memory_id: str,
    body: MemoryUpdate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Memory:
    result = await service.update_memory(
        runtime.storage, actor, workspace_id, memory_id, body, if_match=if_match, settings=runtime.settings.memory
    )
    return tagged(response, result)


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    workspace_id: str, memory_id: str, actor: Actor, runtime: CurrentRuntime, if_match: IfMatch = None
) -> Response:
    await service.delete_memory(runtime.storage, actor, workspace_id, memory_id, if_match=if_match)
    return Response(status_code=204)


@router.get("/{memory_id}/files", response_model=MemoryFilePage)
async def list_files(
    workspace_id: str,
    memory_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    prefix: Annotated[str, Query(max_length=1024, description='A directory ending in "/"; "" lists every file')] = "",
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> MemoryFilePage:
    return await files.list_files(
        runtime.storage,
        actor,
        workspace_id,
        memory_id,
        prefix=prefix,
        limit=limit,
        cursor=cursor,
        settings=runtime.settings.memory,
    )


@router.post("/{memory_id}/files", response_model=MemoryFile, status_code=201)
async def create_file(
    response: Response,
    workspace_id: str,
    memory_id: str,
    body: MemoryFileCreate,
    actor: Actor,
    runtime: CurrentRuntime,
) -> MemoryFile:
    result = await files.create_file(
        runtime.storage, actor, workspace_id, memory_id, body, settings=runtime.settings.memory
    )
    return tagged(response, result)


@router.post("/{memory_id}/files/move", response_model=MemoryFile)
async def move_file(
    response: Response,
    workspace_id: str,
    memory_id: str,
    body: MemoryFileMove,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> MemoryFile:
    """Move the source file `If-Match` names; the destination must be free."""
    result = await files.move_file(
        runtime.storage, actor, workspace_id, memory_id, body, if_match=if_match, settings=runtime.settings.memory
    )
    return tagged(response, result)


@router.get("/{memory_id}/files/{path:path}", response_model=MemoryFile)
async def read_file(
    response: Response, workspace_id: str, memory_id: str, path: str, actor: Actor, runtime: CurrentRuntime
) -> MemoryFile:
    result = await files.read_file(
        runtime.storage, actor, workspace_id, memory_id, path, settings=runtime.settings.memory
    )
    return tagged(response, result)


@router.put("/{memory_id}/files/{path:path}", response_model=MemoryFile)
async def replace_file(
    response: Response,
    workspace_id: str,
    memory_id: str,
    path: str,
    body: MemoryFileReplace,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> MemoryFile:
    result = await files.replace_file(
        runtime.storage,
        actor,
        workspace_id,
        memory_id,
        path,
        body,
        if_match=if_match,
        settings=runtime.settings.memory,
    )
    return tagged(response, result)


@router.delete("/{memory_id}/files/{path:path}", status_code=204)
async def delete_file(
    workspace_id: str,
    memory_id: str,
    path: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Response:
    await files.delete_file(
        runtime.storage, actor, workspace_id, memory_id, path, if_match=if_match, settings=runtime.settings.memory
    )
    return Response(status_code=204)


@router.get("/{memory_id}/revisions", response_model=MemoryRevisionPage)
async def list_revisions(
    workspace_id: str,
    memory_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    path: Annotated[str | None, Query(max_length=1024)] = None,
    run_id: Annotated[str | None, Query(max_length=72)] = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> MemoryRevisionPage:
    return await files.list_revisions(
        runtime.storage, actor, workspace_id, memory_id, path=path, run_id=run_id, limit=limit, cursor=cursor
    )


@router.delete("/{memory_id}/revisions", response_model=HistoryPurge)
async def purge_history(
    workspace_id: str,
    memory_id: str,
    path: Annotated[str, Query(min_length=1, max_length=1024)],
    actor: Actor,
    runtime: CurrentRuntime,
) -> HistoryPurge:
    """Delete every retained revision of one file path."""
    return await files.purge_history(
        runtime.storage, actor, workspace_id, memory_id, path, settings=runtime.settings.memory
    )


@router.get("/{memory_id}/revisions/{seq}", response_model=MemoryRevisionDetail)
async def get_revision(
    workspace_id: str, memory_id: str, seq: int, actor: Actor, runtime: CurrentRuntime
) -> MemoryRevisionDetail:
    return await files.get_revision(runtime.storage, actor, workspace_id, memory_id, seq)


@router.post("/{memory_id}/revisions/{seq}/restore", response_model=MemoryFileState)
async def restore_revision(
    response: Response,
    workspace_id: str,
    memory_id: str,
    seq: int,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> MemoryFileState:
    """Set the path back to the content the change replaced; `If-Match` names the file there, if any."""
    result = await files.restore_revision(
        runtime.storage, actor, workspace_id, memory_id, seq, if_match=if_match, settings=runtime.settings.memory
    )
    if result.file is not None:
        tagged(response, result.file)
    return result
