"""Asset creation from uploads, reads and retirement."""

from fastapi import APIRouter, Response

from a13n_service.infra.http import IfMatch, download_headers, tagged
from a13n_service.resources.assets import service, usage
from a13n_service.resources.assets.schemas import (
    Asset,
    AssetCreate,
    WorkspaceStorage,
)
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1/assets", tags=["assets"])
storage_router = APIRouter(prefix="/api/v1/storage", tags=["assets"])


@storage_router.get("", response_model=WorkspaceStorage, openapi_extra={"x-a13n-mcp": True})
async def get_workspace_storage(workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime) -> WorkspaceStorage:
    """Summarize this workspace's asset and staged upload bytes by creator."""
    return await usage.get_workspace_storage(runtime.storage, actor, workspace_id)


@router.post(
    "",
    response_model=Asset,
    status_code=201,
    responses={200: {"model": Asset, "description": "The asset already created from this upload"}},
    openapi_extra={"x-a13n-mcp": True},
)
async def create_asset(
    response: Response, workspace_id: WorkspaceId, body: AssetCreate, actor: Actor, runtime: CurrentRuntime
) -> Asset:
    result, created = await service.create_asset(runtime.storage, actor, workspace_id, body)
    response.status_code = 201 if created else 200
    return tagged(response, result)


@router.get("/{asset_id}", response_model=Asset, openapi_extra={"x-a13n-mcp": True})
async def get_asset(
    response: Response, workspace_id: WorkspaceId, asset_id: str, actor: Actor, runtime: CurrentRuntime
) -> Asset:
    return tagged(response, await service.get_asset(runtime.storage, actor, workspace_id, asset_id))


@router.delete("/{asset_id}", response_model=Asset, openapi_extra={"x-a13n-mcp": True})
async def retire_asset(
    response: Response,
    workspace_id: WorkspaceId,
    asset_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Asset:
    return tagged(
        response, await service.retire_asset(runtime.storage, actor, workspace_id, asset_id, if_match=if_match)
    )


@router.get(
    "/{asset_id}/content",
    response_class=Response,
    responses={
        200: {
            "description": "The asset bytes, with their stored content type",
            "content": {"*/*": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def read_asset_content(
    workspace_id: WorkspaceId, asset_id: str, actor: Actor, runtime: CurrentRuntime
) -> Response:
    result, data = await service.read_asset_content(runtime.storage, runtime.objects, actor, workspace_id, asset_id)
    return Response(
        data,
        media_type=result.content_type,
        headers=download_headers(result.name),
    )
