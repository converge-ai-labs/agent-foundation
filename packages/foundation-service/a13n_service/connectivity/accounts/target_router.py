"""Exact provider-object configuration under its Account."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.errors import NativeError
from a13n_service.etags import resource_etag
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.request_runtime import get_connectivity_control_runtime

from .target_service import AccountTargetService
from .targets import AccountTarget, ReplaceTargetRequest, TargetCollection, TargetConfig

router = APIRouter(prefix="/api/v1/application-accounts/{account_id}/targets", tags=["connectivity-management"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]


def _service(request: Request) -> AccountTargetService:
    runtime = get_connectivity_control_runtime(request)
    if runtime is None:
        raise NativeError(
            "connectivity_management_unavailable",
            "Connectivity management is unavailable.",
            category=ErrorCategory.unavailable,
        )
    return runtime.targets


def _response(response: Response, resource: AccountTarget) -> AccountTarget:
    response.headers["ETag"] = resource_etag(resource.id, resource.updated_at)
    return resource


@router.post("", response_model=AccountTarget, status_code=201)
async def create(
    request: Request,
    response: Response,
    actor: Actor,
    account_id: str,
    body: TargetConfig,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=512)],
) -> AccountTarget:
    return _response(
        response,
        await _service(request).create(
            actor=actor, account_id=account_id, idempotency_key=idempotency_key, request=body
        ),
    )


@router.get("", response_model=TargetCollection)
async def list_targets(
    request: Request,
    actor: Actor,
    account_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> TargetCollection:
    return await _service(request).list(actor=actor, account_id=account_id, limit=limit, cursor=cursor)


@router.get("/{target_id}", response_model=AccountTarget)
async def get(request: Request, response: Response, actor: Actor, account_id: str, target_id: str) -> AccountTarget:
    return _response(response, await _service(request).get(actor=actor, account_id=account_id, target_id=target_id))


@router.put("/{target_id}", response_model=AccountTarget)
async def replace(
    request: Request, response: Response, actor: Actor, account_id: str, target_id: str, body: ReplaceTargetRequest
) -> AccountTarget:
    return _response(
        response, await _service(request).replace(actor=actor, account_id=account_id, target_id=target_id, request=body)
    )


@router.delete("/{target_id}", status_code=204)
async def delete(
    request: Request, actor: Actor, account_id: str, target_id: str, expected_version: Annotated[int, Query(ge=1)]
) -> Response:
    await _service(request).delete(
        actor=actor, account_id=account_id, target_id=target_id, expected_version=expected_version
    )
    return Response(status_code=204)
