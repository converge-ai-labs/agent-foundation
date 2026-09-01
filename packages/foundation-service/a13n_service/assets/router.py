"""Native Asset Management `/api/v1` routes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from a13n_service.iam import AuthenticatedActor, authenticate_request

from .domain import Asset, AssetCollection, AssetSourceKind
from .errors import AssetError, asset_limit
from .service import AssetService, PreparedAssetContent

router = APIRouter(prefix="/api/v1", tags=["asset-management"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=512)]


def _assets(request: Request) -> AssetService:
    service: AssetService | None = getattr(request.app.state, "asset_service", None)
    if service is None:
        raise AssetError(
            "asset_management_unavailable",
            "Asset Management is unavailable.",
            status_code=503,
        )
    return service


@router.post(
    "/workspaces/{workspace_id}/assets",
    response_model=Asset,
    status_code=status.HTTP_201_CREATED,
)
async def upload_asset(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
    idempotency_key: IdempotencyKey,
    filename: Annotated[str, Query(min_length=1, max_length=1024)],
    media_type: Annotated[str | None, Query(max_length=255)] = None,
) -> Asset:
    _require_octet_stream(request)
    result = await _assets(request).upload(
        actor=actor,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        filename=filename,
        media_type=media_type,
        body=request.stream(),
        content_length=_content_length(request),
    )
    response.status_code = result.status_code
    return result.asset


@router.get("/workspaces/{workspace_id}/assets", response_model=AssetCollection)
async def list_assets(
    request: Request,
    actor: Actor,
    workspace_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    source_kind: AssetSourceKind | None = None,
    source_run_id: Annotated[str | None, Query(max_length=72)] = None,
) -> AssetCollection:
    return await _assets(request).list(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
        source_kind=source_kind,
        source_run_id=source_run_id,
    )


@router.get("/assets/{asset_id}", response_model=Asset)
async def get_asset(request: Request, actor: Actor, asset_id: str) -> Asset:
    return await _assets(request).get(actor=actor, asset_id=asset_id)


@router.get("/assets/{asset_id}/content")
async def get_asset_content(request: Request, actor: Actor, asset_id: str) -> Response:
    prepared = await _assets(request).prepare_content(actor=actor, asset_id=asset_id)
    return StreamingResponse(
        _stream_and_remove(prepared),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": _content_disposition(prepared.asset.filename),
            "Content-Length": str(prepared.asset.size_bytes),
            "ETag": f'"sha256:{prepared.asset.content_sha256}"',
        },
    )


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(request: Request, actor: Actor, asset_id: str) -> Response:
    await _assets(request).delete(actor=actor, asset_id=asset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _require_octet_stream(request: Request) -> None:
    if request.headers.get("content-type", "").strip().lower() != "application/octet-stream":
        raise AssetError(
            "invalid_request",
            "The request body must be exactly application/octet-stream.",
            status_code=400,
        )


def _content_length(request: Request) -> int | None:
    value = request.headers.get("content-length")
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as error:
        raise AssetError("invalid_request", "Content-Length is invalid.", status_code=400) from error
    if parsed < 0:
        raise AssetError("invalid_request", "Content-Length is invalid.", status_code=400)
    settings = request.app.state.settings
    if parsed > settings.asset_max_size_bytes:
        raise asset_limit()
    return parsed


async def _stream_and_remove(prepared: PreparedAssetContent) -> AsyncIterator[bytes]:
    try:
        async for chunk in prepared.content.chunks():
            yield chunk
    finally:
        await prepared.content.remove()


def _content_disposition(filename: str) -> str:
    return f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
