"""Plugin Management public `/api/v1` routes."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from a13n_service.etags import resource_etag
from a13n_service.iam import AuthenticatedActor, authenticate_request

from .domain import (
    Plugin,
    PluginCollection,
    PluginSource,
    PluginTaskReceipt,
    PluginVersion,
    PluginVersionCollection,
)
from .errors import PluginError, plugin_artifact_limit
from .service import PluginService

router = APIRouter(prefix="/api/v1", tags=["plugin-management"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=512)]


def _plugins(request: Request) -> PluginService:
    service: PluginService | None = getattr(request.app.state, "plugin_service", None)
    if service is None:
        raise PluginError("plugin_management_unavailable", "Plugin Management is unavailable.", status_code=503)
    return service


@router.post("/plugins", response_model=PluginVersion, status_code=status.HTTP_201_CREATED)
async def create_plugin(
    request: Request,
    response: Response,
    actor: Actor,
    idempotency_key: IdempotencyKey,
    filename: Annotated[str, Query(min_length=1, max_length=1024)],
) -> PluginVersion:
    _require_octet_stream(request)
    result = await _plugins(request).upload(
        actor=actor,
        idempotency_key=idempotency_key,
        filename=filename,
        body=request.stream(),
        content_length=_content_length(request),
    )
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return result.version


@router.post("/plugins/{plugin_id}/versions", response_model=PluginVersion, status_code=status.HTTP_201_CREATED)
async def create_plugin_version(
    request: Request,
    response: Response,
    actor: Actor,
    plugin_id: str,
    idempotency_key: IdempotencyKey,
    filename: Annotated[str, Query(min_length=1, max_length=1024)],
) -> PluginVersion:
    _require_octet_stream(request)
    result = await _plugins(request).upload(
        actor=actor,
        plugin_id=plugin_id,
        idempotency_key=idempotency_key,
        filename=filename,
        body=request.stream(),
        content_length=_content_length(request),
    )
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return result.version


@router.get("/plugins", response_model=PluginCollection)
async def list_plugins(
    request: Request,
    actor: Actor,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    source: PluginSource | None = None,
    include_archived: bool = False,
) -> PluginCollection:
    return await _plugins(request).list(
        actor=actor,
        limit=limit,
        cursor=cursor,
        source=source,
        include_archived=include_archived,
    )


@router.get("/plugins/{plugin_id}", response_model=Plugin)
async def get_plugin(request: Request, response: Response, actor: Actor, plugin_id: str) -> Plugin:
    plugin = await _plugins(request).get(actor=actor, plugin_id=plugin_id)
    response.headers["ETag"] = resource_etag(plugin.id, plugin.updated_at)
    return plugin


@router.get("/plugins/{plugin_id}/versions", response_model=PluginVersionCollection)
async def list_plugin_versions(
    request: Request,
    actor: Actor,
    plugin_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> PluginVersionCollection:
    return await _plugins(request).list_versions(actor=actor, plugin_id=plugin_id, limit=limit, cursor=cursor)


@router.get("/plugin-versions/{plugin_version_id}", response_model=PluginVersion)
async def get_plugin_version(request: Request, actor: Actor, plugin_version_id: str) -> PluginVersion:
    return await _plugins(request).get_version(actor=actor, version_id=plugin_version_id)


@router.post(
    "/plugin-versions/{plugin_version_id}/activate",
    response_model=PluginTaskReceipt,
    status_code=status.HTTP_202_ACCEPTED,
)
async def activate_plugin_version(
    request: Request,
    actor: Actor,
    plugin_version_id: str,
    idempotency_key: IdempotencyKey,
) -> PluginTaskReceipt:
    return await _plugins(request).activate(
        actor=actor,
        plugin_version_id=plugin_version_id,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/plugins/{plugin_id}/deactivate",
    response_model=PluginTaskReceipt,
    status_code=status.HTTP_202_ACCEPTED,
)
async def deactivate_plugin(
    request: Request,
    actor: Actor,
    plugin_id: str,
    idempotency_key: IdempotencyKey,
) -> PluginTaskReceipt:
    return await _plugins(request).deactivate(
        actor=actor,
        plugin_id=plugin_id,
        idempotency_key=idempotency_key,
    )


@router.get("/operations/{operation_id}", response_model=PluginTaskReceipt)
async def get_plugin_operation(request: Request, actor: Actor, operation_id: str) -> PluginTaskReceipt:
    return await _plugins(request).get_operation(actor=actor, operation_id=operation_id)


@router.post("/plugins/{plugin_id}/{action}", response_model=Plugin)
async def change_plugin_lifecycle(
    request: Request,
    actor: Actor,
    plugin_id: str,
    action: Literal["archive", "unarchive"],
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> Plugin:
    return await _plugins(request).change_lifecycle(
        actor=actor,
        plugin_id=plugin_id,
        action=action,
        idempotency_key=idempotency_key,
        if_match=if_match,
    )


def _require_octet_stream(request: Request) -> None:
    if request.headers.get("content-type", "").strip().lower() != "application/octet-stream":
        raise PluginError(
            "invalid_request", "The request body must be exactly application/octet-stream.", status_code=400
        )


def _content_length(request: Request) -> int | None:
    value = request.headers.get("content-length")
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as error:
        raise PluginError("invalid_request", "Content-Length is invalid.", status_code=400) from error
    if parsed < 0:
        raise PluginError("invalid_request", "Content-Length is invalid.", status_code=400)
    settings = request.app.state.settings
    if parsed > settings.plugin_max_wheel_bytes:
        raise plugin_artifact_limit()
    return parsed
