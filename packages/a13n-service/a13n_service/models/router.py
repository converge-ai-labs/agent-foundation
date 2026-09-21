"""Model Management public `/api/v1` routes."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from a13n_service.application_errors import ErrorCategory
from a13n_service.etags import resource_etag
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.iam.http.resource_dependencies import OrganizationId, WorkspaceId
from a13n_service.iam.resource_routes import require_organization_boundary
from a13n_service.provider_metadata import ProviderMetadataCollection
from a13n_service.request_runtime import get_control_runtime

from .domain import (
    CreateModelProviderRequest,
    CreateModelRequest,
    MediaUnderstandingSelection,
    Model,
    ModelCatalogCollection,
    ModelCollection,
    ModelConnectionTestResult,
    ModelProvider,
    ModelProviderCollection,
    ModelTestRequest,
    UpdateModelProviderRequest,
    UpdateModelRequest,
)
from .media_defaults import MediaUnderstandingDefaults
from .provider_service import ModelProviderService
from .providers import ModelProviderMetadata
from .service import ModelService
from .service_common import ModelError

router = APIRouter(prefix="/api/v1", tags=["model-management"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=1, max_length=256)]


def _model_service(request: Request) -> ModelService:
    control = get_control_runtime(request)
    if control is None:
        raise ModelError(
            "model_management_unavailable", "Model Management is unavailable.", category=ErrorCategory.unavailable
        )
    return control.models


def _provider_service(request: Request) -> ModelProviderService:
    control = get_control_runtime(request)
    if control is None:
        raise ModelError(
            "model_management_unavailable", "Model Management is unavailable.", category=ErrorCategory.unavailable
        )
    return control.model_providers


def _set_etag(response: Response, resource: Model | ModelProvider) -> None:
    response.headers["ETag"] = resource_etag(resource.id, resource.updated_at)


@router.get("/workspaces/{workspace}/media-understanding-defaults")
async def get_media_understanding_defaults(
    request: Request, response: Response, actor: Actor, workspace_id: WorkspaceId
) -> MediaUnderstandingDefaults:
    defaults = await _model_service(request).media_defaults(actor=actor, workspace_id=workspace_id)
    response.headers["ETag"] = defaults.etag()
    return defaults


@router.put("/workspaces/{workspace}/media-understanding-defaults")
async def replace_media_understanding_defaults(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: WorkspaceId,
    body: MediaUnderstandingSelection,
    if_match: IfMatch,
) -> MediaUnderstandingDefaults:
    defaults = await _model_service(request).replace_media_defaults(
        actor=actor, workspace_id=workspace_id, request=body, if_match=if_match
    )
    response.headers["ETag"] = defaults.etag()
    return defaults


@router.get("/model-provider-types")
async def list_model_provider_types(
    request: Request, actor: Actor
) -> ProviderMetadataCollection[ModelProviderMetadata]:
    return await _provider_service(request).provider_types(actor=actor)


@router.get("/model-provider-types/{provider_type}")
async def get_model_provider_type(request: Request, actor: Actor, provider_type: str) -> ModelProviderMetadata:
    return await _provider_service(request).provider_type(actor=actor, provider_type=provider_type)


@router.get("/workspaces/{workspace}/model-providers", response_model=ModelProviderCollection)
async def list_model_providers(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
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
    "/workspaces/{workspace}/model-providers",
    response_model=ModelProvider,
    status_code=status.HTTP_201_CREATED,
)
async def create_model_provider(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: WorkspaceId,
    body: CreateModelProviderRequest,
) -> ModelProvider:
    provider = await _provider_service(request).create(actor=actor, workspace_id=workspace_id, request=body)
    _set_etag(response, provider)
    return provider


@router.get("/workspaces/{workspace}/model-providers/{provider_id}", response_model=ModelProvider)
async def get_model_provider(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: WorkspaceId,
    provider_id: str,
) -> ModelProvider:
    provider = await _provider_service(request).get(
        actor=actor,
        workspace_id=workspace_id,
        provider_id=provider_id,
    )
    _set_etag(response, provider)
    return provider


@router.patch("/workspaces/{workspace}/model-providers/{provider_id}", response_model=ModelProvider)
async def update_model_provider(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: WorkspaceId,
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
    "/workspaces/{workspace}/model-providers/{provider_id}/test",
    response_model=ModelConnectionTestResult,
)
async def test_model_provider(
    request: Request, actor: Actor, workspace_id: WorkspaceId, provider_id: str
) -> ModelConnectionTestResult:
    return await _provider_service(request).test(actor=actor, workspace_id=workspace_id, provider_id=provider_id)


@router.get("/workspaces/{workspace}/models", response_model=ModelCollection)
async def list_models(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    query: Annotated[str | None, Query(max_length=128)] = None,
    provider_id: Annotated[str | None, Query(max_length=72)] = None,
    enabled: bool | None = None,
    scope: Literal["organization", "workspace"] | None = None,
) -> ModelCollection:
    return await _model_service(request).list(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
        query_text=query,
        provider_id=provider_id,
        enabled=enabled,
        owner_scope=scope,
    )


@router.get("/workspaces/{workspace}/model-catalog", response_model=ModelCatalogCollection)
async def list_model_catalog(request: Request, actor: Actor, workspace_id: WorkspaceId) -> ModelCatalogCollection:
    return await _model_service(request).catalog_models(actor=actor, workspace_id=workspace_id)


@router.post("/workspaces/{workspace}/models", response_model=Model, status_code=status.HTTP_201_CREATED)
async def create_model(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: WorkspaceId,
    body: CreateModelRequest,
) -> Model:
    model = await _model_service(request).create(actor=actor, workspace_id=workspace_id, request=body)
    _set_etag(response, model)
    return model


@router.get("/workspaces/{workspace}/models/{model_id}", response_model=Model)
async def get_model(
    request: Request, response: Response, actor: Actor, workspace_id: WorkspaceId, model_id: str
) -> Model:
    model = await _model_service(request).get(actor=actor, workspace_id=workspace_id, model_id=model_id)
    _set_etag(response, model)
    return model


@router.patch("/workspaces/{workspace}/models/{model_id}", response_model=Model)
async def update_model(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: WorkspaceId,
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


@router.post("/workspaces/{workspace}/models/{model_id}/test", response_model=ModelConnectionTestResult)
async def test_model(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    model_id: str,
    body: ModelTestRequest | None = None,
) -> ModelConnectionTestResult:
    return await _model_service(request).test(
        actor=actor,
        workspace_id=workspace_id,
        model_id=model_id,
    )


@router.get("/organizations/{organization}/model-providers", response_model=ModelProviderCollection)
async def organization_list_model_providers(
    request: Request,
    actor: Actor,
    organization_id: OrganizationId,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    name: Annotated[str | None, Query(max_length=128)] = None,
    provider_type: Annotated[str | None, Query(max_length=64)] = None,
    enabled: bool | None = None,
) -> ModelProviderCollection:
    require_organization_boundary(actor, organization_id)
    return await _provider_service(request).list(
        actor=actor,
        workspace_id=None,
        limit=limit,
        cursor=cursor,
        name=name,
        provider_type=provider_type,
        enabled=enabled,
    )


@router.post(
    "/organizations/{organization}/model-providers",
    response_model=ModelProvider,
    status_code=status.HTTP_201_CREATED,
)
async def organization_create_model_provider(
    request: Request,
    response: Response,
    actor: Actor,
    organization_id: OrganizationId,
    body: CreateModelProviderRequest,
) -> ModelProvider:
    require_organization_boundary(actor, organization_id)
    provider = await _provider_service(request).create(actor=actor, workspace_id=None, request=body)
    _set_etag(response, provider)
    return provider


@router.get("/organizations/{organization}/model-providers/{provider_id}", response_model=ModelProvider)
async def organization_get_model_provider(
    request: Request,
    response: Response,
    actor: Actor,
    organization_id: OrganizationId,
    provider_id: str,
) -> ModelProvider:
    require_organization_boundary(actor, organization_id)
    provider = await _provider_service(request).get(
        actor=actor,
        workspace_id=None,
        provider_id=provider_id,
    )
    _set_etag(response, provider)
    return provider


@router.patch("/organizations/{organization}/model-providers/{provider_id}", response_model=ModelProvider)
async def organization_update_model_provider(
    request: Request,
    response: Response,
    actor: Actor,
    organization_id: OrganizationId,
    provider_id: str,
    body: UpdateModelProviderRequest,
    if_match: IfMatch,
) -> ModelProvider:
    require_organization_boundary(actor, organization_id)
    provider = await _provider_service(request).update(
        actor=actor,
        workspace_id=None,
        provider_id=provider_id,
        if_match=if_match,
        request=body,
    )
    _set_etag(response, provider)
    return provider


@router.post(
    "/organizations/{organization}/model-providers/{provider_id}/test",
    response_model=ModelConnectionTestResult,
)
async def organization_test_model_provider(
    request: Request, actor: Actor, organization_id: OrganizationId, provider_id: str
) -> ModelConnectionTestResult:
    require_organization_boundary(actor, organization_id)
    return await _provider_service(request).test(actor=actor, workspace_id=None, provider_id=provider_id)


@router.get("/organizations/{organization}/models", response_model=ModelCollection)
async def organization_list_models(
    request: Request,
    actor: Actor,
    organization_id: OrganizationId,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    query: Annotated[str | None, Query(max_length=128)] = None,
    provider_id: Annotated[str | None, Query(max_length=72)] = None,
    enabled: bool | None = None,
) -> ModelCollection:
    require_organization_boundary(actor, organization_id)
    return await _model_service(request).list(
        actor=actor,
        workspace_id=None,
        limit=limit,
        cursor=cursor,
        query_text=query,
        provider_id=provider_id,
        enabled=enabled,
    )


@router.get("/organizations/{organization}/model-catalog", response_model=ModelCatalogCollection)
async def organization_list_model_catalog(
    request: Request, actor: Actor, organization_id: OrganizationId
) -> ModelCatalogCollection:
    require_organization_boundary(actor, organization_id)
    return await _model_service(request).catalog_models(actor=actor, workspace_id=None)


@router.post("/organizations/{organization}/models", response_model=Model, status_code=status.HTTP_201_CREATED)
async def organization_create_model(
    request: Request,
    response: Response,
    actor: Actor,
    organization_id: OrganizationId,
    body: CreateModelRequest,
) -> Model:
    require_organization_boundary(actor, organization_id)
    model = await _model_service(request).create(actor=actor, workspace_id=None, request=body)
    _set_etag(response, model)
    return model


@router.get("/organizations/{organization}/models/{model_id}", response_model=Model)
async def organization_get_model(
    request: Request, response: Response, actor: Actor, organization_id: OrganizationId, model_id: str
) -> Model:
    require_organization_boundary(actor, organization_id)
    model = await _model_service(request).get(actor=actor, workspace_id=None, model_id=model_id)
    _set_etag(response, model)
    return model


@router.patch("/organizations/{organization}/models/{model_id}", response_model=Model)
async def organization_update_model(
    request: Request,
    response: Response,
    actor: Actor,
    organization_id: OrganizationId,
    model_id: str,
    body: UpdateModelRequest,
    if_match: IfMatch,
) -> Model:
    require_organization_boundary(actor, organization_id)
    model = await _model_service(request).update(
        actor=actor,
        workspace_id=None,
        model_id=model_id,
        if_match=if_match,
        request=body,
    )
    _set_etag(response, model)
    return model


@router.post("/organizations/{organization}/models/{model_id}/test", response_model=ModelConnectionTestResult)
async def organization_test_model(
    request: Request,
    actor: Actor,
    organization_id: OrganizationId,
    model_id: str,
    body: ModelTestRequest | None = None,
) -> ModelConnectionTestResult:
    require_organization_boundary(actor, organization_id)
    return await _model_service(request).test(
        actor=actor,
        workspace_id=None,
        model_id=model_id,
    )
