"""Model Management public `/api/v1` routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from a13n_service.etags import resource_etag
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.process.runtime import get_control_runtime

from .domain import (
    CreateModelProviderRequest,
    CreateModelRequest,
    Model,
    ModelCollection,
    ModelConnectionTestResult,
    ModelProvider,
    ModelProviderCollection,
    TestModelRequest,
    UpdateModelProviderRequest,
    UpdateModelRequest,
)
from .provider_service import ModelProviderService
from .providers import DiscoveredModelCollection, ModelProviderTypeDefinitionCollection
from .service import ModelService
from .service_common import ModelError

router = APIRouter(prefix="/api/v1", tags=["model-management"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=1, max_length=256)]


def _model_service(request: Request) -> ModelService:
    control = get_control_runtime(request)
    if control is None:
        raise ModelError("model_management_unavailable", "Model Management is unavailable.", status_code=503)
    return control.models


def _provider_service(request: Request) -> ModelProviderService:
    control = get_control_runtime(request)
    if control is None:
        raise ModelError("model_management_unavailable", "Model Management is unavailable.", status_code=503)
    return control.model_providers


def _set_etag(response: Response, resource: Model | ModelProvider) -> None:
    response.headers["ETag"] = resource_etag(resource.id, resource.updated_at)


@router.get("/model-provider-types", response_model=ModelProviderTypeDefinitionCollection)
async def list_model_provider_types(request: Request, actor: Actor) -> ModelProviderTypeDefinitionCollection:
    return await _provider_service(request).type_definitions(actor=actor)


@router.get("/workspaces/{workspace_id}/model-providers", response_model=ModelProviderCollection)
async def list_model_providers(
    request: Request,
    actor: Actor,
    workspace_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    name: Annotated[str | None, Query(max_length=128)] = None,
    provider_type: Annotated[str | None, Query(max_length=64)] = None,
    enabled: bool | None = None,
) -> ModelProviderCollection:
    return await _provider_service(request).list(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
        name=name,
        provider_type=provider_type,
        enabled=enabled,
    )


@router.post(
    "/workspaces/{workspace_id}/model-providers",
    response_model=ModelProvider,
    status_code=status.HTTP_201_CREATED,
)
async def create_model_provider(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
    body: CreateModelProviderRequest,
) -> ModelProvider:
    provider = await _provider_service(request).create(actor=actor, workspace_id=workspace_id, request=body)
    _set_etag(response, provider)
    return provider


@router.get("/workspaces/{workspace_id}/model-providers/{provider_id}", response_model=ModelProvider)
async def get_model_provider(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
    provider_id: str,
) -> ModelProvider:
    provider = await _provider_service(request).get(
        actor=actor,
        workspace_id=workspace_id,
        provider_id=provider_id,
    )
    _set_etag(response, provider)
    return provider


@router.patch("/workspaces/{workspace_id}/model-providers/{provider_id}", response_model=ModelProvider)
async def update_model_provider(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
    provider_id: str,
    body: UpdateModelProviderRequest,
    if_match: IfMatch,
) -> ModelProvider:
    provider = await _provider_service(request).update(
        actor=actor,
        workspace_id=workspace_id,
        provider_id=provider_id,
        if_match=if_match,
        request=body,
    )
    _set_etag(response, provider)
    return provider


@router.post(
    "/workspaces/{workspace_id}/model-providers/{provider_id}/discover-models",
    response_model=DiscoveredModelCollection,
)
async def discover_provider_models(
    request: Request, actor: Actor, workspace_id: str, provider_id: str
) -> DiscoveredModelCollection:
    return await _provider_service(request).discover_models(
        actor=actor,
        workspace_id=workspace_id,
        provider_id=provider_id,
    )


@router.post(
    "/workspaces/{workspace_id}/model-providers/{provider_id}/test",
    response_model=ModelConnectionTestResult,
)
async def test_model_provider(
    request: Request, actor: Actor, workspace_id: str, provider_id: str
) -> ModelConnectionTestResult:
    return await _provider_service(request).test(actor=actor, workspace_id=workspace_id, provider_id=provider_id)


@router.get("/workspaces/{workspace_id}/models", response_model=ModelCollection)
async def list_models(
    request: Request,
    actor: Actor,
    workspace_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    query: Annotated[str | None, Query(max_length=128)] = None,
    provider_id: Annotated[str | None, Query(max_length=72)] = None,
    enabled: bool | None = None,
) -> ModelCollection:
    return await _model_service(request).list(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
        query_text=query,
        provider_id=provider_id,
        enabled=enabled,
    )


@router.post("/workspaces/{workspace_id}/models", response_model=Model, status_code=status.HTTP_201_CREATED)
async def create_model(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
    body: CreateModelRequest,
) -> Model:
    model = await _model_service(request).create(actor=actor, workspace_id=workspace_id, request=body)
    _set_etag(response, model)
    return model


@router.get("/workspaces/{workspace_id}/models/{model_id}", response_model=Model)
async def get_model(request: Request, response: Response, actor: Actor, workspace_id: str, model_id: str) -> Model:
    model = await _model_service(request).get(actor=actor, workspace_id=workspace_id, model_id=model_id)
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
    model = await _model_service(request).update(
        actor=actor,
        workspace_id=workspace_id,
        model_id=model_id,
        if_match=if_match,
        request=body,
    )
    _set_etag(response, model)
    return model


@router.post("/workspaces/{workspace_id}/models/{model_id}/test", response_model=ModelConnectionTestResult)
async def test_model(
    request: Request,
    actor: Actor,
    workspace_id: str,
    model_id: str,
    body: TestModelRequest,
) -> ModelConnectionTestResult:
    return await _model_service(request).test(
        actor=actor,
        workspace_id=workspace_id,
        model_id=model_id,
        model_api=body.model_api,
    )
