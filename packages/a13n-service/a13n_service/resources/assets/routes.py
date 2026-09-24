"""Asset creation from uploads, reads and retirement."""

from fastapi import APIRouter, Response

from a13n_service.infra.http import IfMatch, PageLimit, download_headers, tagged
from a13n_service.resources.assets import service
from a13n_service.resources.assets.schemas import Asset, AssetCreate, AssetPage
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/assets", tags=["assets"])


@router.post(
    "",
    response_model=Asset,
    status_code=201,
    responses={200: {"model": Asset, "description": "The asset already created from this upload"}},
)
async def create_asset(
    response: Response, workspace_id: str, body: AssetCreate, actor: Actor, runtime: CurrentRuntime
) -> Asset:
    result, created = await service.create_asset(runtime.storage, runtime.objects, actor, workspace_id, body)
    response.status_code = 201 if created else 200
    return tagged(response, result)


@router.get("", response_model=AssetPage)
async def list_assets(
    workspace_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> AssetPage:
    return await service.list_assets(runtime.storage, actor, workspace_id, limit=limit, cursor=cursor)


@router.get("/{asset_id}", response_model=Asset)
async def get_asset(
    response: Response, workspace_id: str, asset_id: str, actor: Actor, runtime: CurrentRuntime
) -> Asset:
    return tagged(response, await service.get_asset(runtime.storage, actor, workspace_id, asset_id))


@router.delete("/{asset_id}", response_model=Asset)
async def retire_asset(
    response: Response,
    workspace_id: str,
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
async def read_asset_content(workspace_id: str, asset_id: str, actor: Actor, runtime: CurrentRuntime) -> Response:
    result, data = await service.read_asset_content(runtime.storage, runtime.objects, actor, workspace_id, asset_id)
    return Response(
        data,
        media_type=result.content_type,
        headers=download_headers(result.name),
    )
