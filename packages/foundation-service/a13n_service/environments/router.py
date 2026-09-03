"""Foundation Environment Management routes under `/api/v1`."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from a13n_service.etags import resource_etag
from a13n_service.iam import AuthenticatedActor, authenticate_request

from .domain import (
    CreateEnvironmentRequest,
    CreateEnvironmentRevisionRequest,
    Environment,
    EnvironmentCollection,
    EnvironmentProviderCatalogEntry,
    EnvironmentProviderCatalogEntryCollection,
    EnvironmentProviderSelection,
    EnvironmentRevision,
    EnvironmentRevisionCollection,
    EnvironmentRevisionTestResult,
    PutEnvironmentProviderSelectionRequest,
    UpdateEnvironmentRequest,
)
from .errors import EnvironmentManagementError
from .service import EnvironmentManagementService

router = APIRouter(prefix="/api/v1", tags=["environment-management"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=512)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=1, max_length=256)]


def _service(request: Request) -> EnvironmentManagementService:
    service: EnvironmentManagementService | None = getattr(request.app.state, "environment_service", None)
    if service is None:
        raise EnvironmentManagementError(
            "environment_management_unavailable",
            "Environment Management is unavailable.",
            status_code=503,
        )
    return service


@router.get("/environment-providers", response_model=EnvironmentProviderCatalogEntryCollection)
async def list_environment_providers(request: Request, actor: Actor) -> EnvironmentProviderCatalogEntryCollection:
    return await _service(request).list_provider_catalog(actor=actor)


@router.get("/environment-providers/{provider_key}", response_model=EnvironmentProviderCatalogEntry)
async def get_environment_provider(
    request: Request,
    actor: Actor,
    provider_key: str,
) -> EnvironmentProviderCatalogEntry:
    return await _service(request).get_provider_catalog_entry(actor=actor, provider_key=provider_key)


@router.get(
    "/workspaces/{workspace_id}/environment-providers/{provider_key}",
    response_model=EnvironmentProviderSelection,
)
async def get_environment_provider_selection(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
    provider_key: str,
) -> EnvironmentProviderSelection:
    selection = await _service(request).get_provider_selection(
        actor=actor,
        workspace_id=workspace_id,
        provider_key=provider_key,
    )
    response.headers["ETag"] = resource_etag(f"{workspace_id}:{provider_key}", selection.updated_at)
    return selection


@router.put(
    "/workspaces/{workspace_id}/environment-providers/{provider_key}",
    response_model=EnvironmentProviderSelection,
)
async def put_environment_provider_selection(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
    provider_key: str,
    body: PutEnvironmentProviderSelectionRequest,
    if_match: Annotated[str | None, Header(alias="If-Match", max_length=256)] = None,
) -> EnvironmentProviderSelection:
    selection = await _service(request).put_provider_selection(
        actor=actor,
        workspace_id=workspace_id,
        provider_key=provider_key,
        if_match=if_match,
        request=body,
    )
    response.headers["ETag"] = resource_etag(f"{workspace_id}:{provider_key}", selection.updated_at)
    return selection


@router.post(
    "/workspaces/{workspace_id}/environments",
    response_model=Environment,
    status_code=status.HTTP_201_CREATED,
)
async def create_environment(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
    body: CreateEnvironmentRequest,
    idempotency_key: IdempotencyKey,
) -> Environment:
    environment = await _service(request).create(
        actor=actor,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        request=body,
    )
    response.headers["ETag"] = resource_etag(environment.id, environment.updated_at)
    return environment


@router.get("/workspaces/{workspace_id}/environments", response_model=EnvironmentCollection)
async def list_environments(
    request: Request,
    actor: Actor,
    workspace_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    include_archived: bool = False,
) -> EnvironmentCollection:
    return await _service(request).list(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
        include_archived=include_archived,
    )


@router.get("/environments/{environment_id}", response_model=Environment)
async def get_environment(request: Request, response: Response, actor: Actor, environment_id: str) -> Environment:
    environment = await _service(request).get(actor=actor, environment_id=environment_id)
    response.headers["ETag"] = resource_etag(environment.id, environment.updated_at)
    return environment


@router.patch("/environments/{environment_id}", response_model=Environment)
async def patch_environment(
    request: Request,
    response: Response,
    actor: Actor,
    environment_id: str,
    body: UpdateEnvironmentRequest,
    if_match: IfMatch,
) -> Environment:
    environment = await _service(request).patch(
        actor=actor, environment_id=environment_id, if_match=if_match, request=body
    )
    response.headers["ETag"] = resource_etag(environment.id, environment.updated_at)
    return environment


@router.post(
    "/environments/{environment_id}/revisions",
    response_model=EnvironmentRevision,
    status_code=status.HTTP_201_CREATED,
)
async def create_environment_revision(
    request: Request,
    response: Response,
    actor: Actor,
    environment_id: str,
    body: CreateEnvironmentRevisionRequest,
    idempotency_key: IdempotencyKey,
) -> EnvironmentRevision:
    result = await _service(request).create_revision(
        actor=actor,
        environment_id=environment_id,
        idempotency_key=idempotency_key,
        request=body,
    )
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    response.headers["Cache-Control"] = "private, no-store"
    return result.revision


@router.get(
    "/environments/{environment_id}/revisions",
    response_model=EnvironmentRevisionCollection,
)
async def list_environment_revisions(
    request: Request,
    actor: Actor,
    environment_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> EnvironmentRevisionCollection:
    return await _service(request).list_revisions(
        actor=actor,
        environment_id=environment_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/environment-revisions/{environment_revision_id}", response_model=EnvironmentRevision)
async def get_environment_revision(
    request: Request,
    response: Response,
    actor: Actor,
    environment_revision_id: str,
) -> EnvironmentRevision:
    revision = await _service(request).get_revision(actor=actor, revision_id=environment_revision_id)
    response.headers["Cache-Control"] = "private, no-store"
    return revision


@router.post(
    "/environment-revisions/{environment_revision_id}/test",
    response_model=EnvironmentRevisionTestResult,
)
async def test_environment_revision(
    request: Request,
    response: Response,
    actor: Actor,
    environment_revision_id: str,
) -> EnvironmentRevisionTestResult:
    result = await _service(request).test_revision(actor=actor, revision_id=environment_revision_id)
    response.headers["Cache-Control"] = "private, no-store"
    return result
