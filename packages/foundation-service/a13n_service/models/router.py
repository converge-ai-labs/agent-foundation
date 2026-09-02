"""Model Management public `/api/v1` routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from a13n_service.etags import resource_etag
from a13n_service.iam import AuthenticatedActor, authenticate_request

from .domain import (
    CreateModelRequest,
    CreateModelRevisionRequest,
    Model,
    ModelCollection,
    ModelConnectionTestResult,
    ModelRevision,
    ModelRevisionCollection,
    ModelRevisionCreateResult,
    ModelRevisionInput,
    UpdateModelRequest,
)
from .providers import ProviderDefinitionCollection
from .service import ModelError, ModelService

router = APIRouter(prefix="/api/v1", tags=["model-management"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=1, max_length=256)]


def _service(request: Request) -> ModelService:
    service: ModelService | None = getattr(request.app.state, "model_service", None)
    if service is None:
        raise ModelError("model_management_unavailable", "Model Management is unavailable.", status_code=503)
    return service


def _set_etag(response: Response, model: Model) -> None:
    response.headers["ETag"] = resource_etag(model.id, model.updated_at)


@router.get("/model-providers", response_model=ProviderDefinitionCollection)
async def list_model_providers(request: Request, actor: Actor) -> ProviderDefinitionCollection:
    return await _service(request).provider_definitions(actor=actor)


@router.get("/workspaces/{workspace_id}/models", response_model=ModelCollection)
async def list_models(
    request: Request,
    actor: Actor,
    workspace_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    name: Annotated[str | None, Query(max_length=128)] = None,
    provider_type: Annotated[str | None, Query(max_length=64)] = None,
    enabled: bool | None = None,
) -> ModelCollection:
    return await _service(request).list(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
        name=name,
        provider_type=provider_type,
        enabled=enabled,
    )


@router.post(
    "/workspaces/{workspace_id}/models",
    response_model=ModelRevisionCreateResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_model(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
    body: CreateModelRequest,
) -> ModelRevisionCreateResult:
    result = await _service(request).create(actor=actor, workspace_id=workspace_id, request=body)
    _set_etag(response, result.model)
    return result


@router.get("/workspaces/{workspace_id}/models/{model_id}", response_model=Model)
async def get_model(request: Request, response: Response, actor: Actor, workspace_id: str, model_id: str) -> Model:
    model = await _service(request).get(actor=actor, workspace_id=workspace_id, model_id=model_id)
    _set_etag(response, model)
    return model


@router.patch("/workspaces/{workspace_id}/models/{model_id}", response_model=Model)
async def update_model(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
    model_id: str,
    body: UpdateModelRequest,
    if_match: IfMatch,
) -> Model:
    model = await _service(request).update(
        actor=actor,
        workspace_id=workspace_id,
        model_id=model_id,
        if_match=if_match,
        request=body,
    )
    _set_etag(response, model)
    return model


@router.post(
    "/workspaces/{workspace_id}/models/{model_id}/revisions",
    response_model=ModelRevisionCreateResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_model_revision(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
    model_id: str,
    body: CreateModelRevisionRequest,
) -> ModelRevisionCreateResult:
    result = await _service(request).create_revision(
        actor=actor,
        workspace_id=workspace_id,
        model_id=model_id,
        request=body,
    )
    _set_etag(response, result.model)
    return result


@router.get(
    "/workspaces/{workspace_id}/models/{model_id}/revisions",
    response_model=ModelRevisionCollection,
)
async def list_model_revisions(
    request: Request, actor: Actor, workspace_id: str, model_id: str
) -> ModelRevisionCollection:
    return await _service(request).list_revisions(actor=actor, workspace_id=workspace_id, model_id=model_id)


@router.get("/workspaces/{workspace_id}/model-revisions/{revision_id}", response_model=ModelRevision)
async def get_model_revision(request: Request, actor: Actor, workspace_id: str, revision_id: str) -> ModelRevision:
    return await _service(request).get_revision(actor=actor, workspace_id=workspace_id, revision_id=revision_id)


@router.post("/workspaces/{workspace_id}/models/test", response_model=ModelConnectionTestResult)
async def test_model_candidate(
    request: Request, actor: Actor, workspace_id: str, body: ModelRevisionInput
) -> ModelConnectionTestResult:
    return await _service(request).test_candidate(actor=actor, workspace_id=workspace_id, request=body)
