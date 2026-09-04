"""Ingress and Route management routes under ``/api/v1``."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from a13n_service.etags import resource_etag
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.request_runtime import get_connectivity_control_runtime

from .domain import (
    CreateIngressRequest,
    CreateRouteRequest,
    Ingress,
    IngressCollection,
    IngressCommandRequest,
    IngressStatus,
    Route,
    RouteCollection,
    UpdateIngressRequest,
    UpdateRouteRequest,
)
from .routes import RouteService
from .service import IngressService, NativeError

router = APIRouter(prefix="/api/v1", tags=["connectivity-management"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=512)]


def _service(request: Request) -> IngressService:
    runtime = get_connectivity_control_runtime(request)
    if runtime is None:
        raise NativeError("ingress_management_unavailable", "Ingress Management is unavailable.", status_code=503)
    return runtime.ingresses


def _routes(request: Request) -> RouteService:
    runtime = get_connectivity_control_runtime(request)
    if runtime is None:
        raise NativeError("route_management_unavailable", "Route Management is unavailable.", status_code=503)
    return runtime.routes


def _set_etag(response: Response, resource: Ingress | Route) -> None:
    response.headers["ETag"] = resource_etag(resource.id, resource.updated_at)


@router.post(
    "/workspaces/{workspace_id}/ingresses",
    response_model=Ingress,
    status_code=status.HTTP_201_CREATED,
)
async def create_ingress(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
    body: CreateIngressRequest,
    idempotency_key: IdempotencyKey,
) -> Ingress:
    resource = await _service(request).create_ingress(
        actor=actor,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        request=body,
    )
    _set_etag(response, resource)
    return resource


@router.get("/workspaces/{workspace_id}/ingresses", response_model=IngressCollection)
async def list_ingresses(
    request: Request,
    actor: Actor,
    workspace_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> IngressCollection:
    return await _service(request).list_ingresses(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/ingresses/{ingress_id}", response_model=Ingress)
async def get_ingress(request: Request, response: Response, actor: Actor, ingress_id: str) -> Ingress:
    resource = await _service(request).get_ingress(actor=actor, ingress_id=ingress_id)
    _set_etag(response, resource)
    return resource


@router.patch("/ingresses/{ingress_id}", response_model=Ingress)
async def update_ingress(
    request: Request,
    response: Response,
    actor: Actor,
    ingress_id: str,
    body: UpdateIngressRequest,
) -> Ingress:
    resource = await _service(request).update_ingress(actor=actor, ingress_id=ingress_id, request=body)
    _set_etag(response, resource)
    return resource


@router.post("/ingresses/{ingress_id}/{action}", response_model=Ingress)
async def change_ingress_lifecycle(
    request: Request,
    response: Response,
    actor: Actor,
    ingress_id: str,
    action: Literal["enable", "disable"],
    body: IngressCommandRequest,
    idempotency_key: IdempotencyKey,
) -> Ingress:
    target = IngressStatus.active if action == "enable" else IngressStatus.disabled
    resource = await _service(request).set_status(
        actor=actor,
        ingress_id=ingress_id,
        status=target,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
    )
    _set_etag(response, resource)
    return resource


@router.post("/ingresses/{ingress_id}/routes", response_model=Route, status_code=status.HTTP_201_CREATED)
async def create_route(
    request: Request,
    response: Response,
    actor: Actor,
    ingress_id: str,
    body: CreateRouteRequest,
    idempotency_key: IdempotencyKey,
) -> Route:
    resource = await _routes(request).create_route(
        actor=actor,
        ingress_id=ingress_id,
        idempotency_key=idempotency_key,
        request=body,
    )
    _set_etag(response, resource)
    return resource


@router.get("/ingresses/{ingress_id}/routes", response_model=RouteCollection)
async def list_routes(
    request: Request,
    actor: Actor,
    ingress_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> RouteCollection:
    return await _routes(request).list_routes(actor=actor, ingress_id=ingress_id, limit=limit, cursor=cursor)


@router.get("/routes/{route_id}", response_model=Route)
async def get_route(request: Request, response: Response, actor: Actor, route_id: str) -> Route:
    resource = await _routes(request).get_route(actor=actor, route_id=route_id)
    _set_etag(response, resource)
    return resource


@router.patch("/routes/{route_id}", response_model=Route)
async def update_route(
    request: Request,
    response: Response,
    actor: Actor,
    route_id: str,
    body: UpdateRouteRequest,
) -> Route:
    resource = await _routes(request).update_route(actor=actor, route_id=route_id, request=body)
    _set_etag(response, resource)
    return resource
