"""Model Management public `/api/v1` routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from a13n_service.iam import AuthenticatedActor, authenticate_request

from .domain import (
    ModelConfigCollection,
    ModelConfigCreate,
    ModelConfigPatch,
    ModelConfigResource,
    ModelConnectionTestResult,
)
from .providers import ProviderDefinitionCollection
from .service import ModelConfigService, ModelManagementError

router = APIRouter(prefix="/api/v1", tags=["model-management"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]


def _service(request: Request) -> ModelConfigService:
    service: ModelConfigService | None = getattr(request.app.state, "model_config_service", None)
    if service is None:
        raise ModelManagementError("model_management_unavailable", "Model Management is unavailable.", status_code=503)
    return service


@router.get("/model-providers", response_model=ProviderDefinitionCollection)
async def list_model_providers(request: Request, actor: Actor) -> ProviderDefinitionCollection:
    return await _service(request).provider_definitions(actor=actor)


@router.get("/workspaces/{workspace_id}/models", response_model=ModelConfigCollection)
async def list_models(
    request: Request,
    actor: Actor,
    workspace_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    name: Annotated[str | None, Query(max_length=128)] = None,
    provider_type: Annotated[str | None, Query(max_length=64)] = None,
    enabled: bool | None = None,
) -> ModelConfigCollection:
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
    response_model=ModelConfigResource,
    status_code=status.HTTP_201_CREATED,
)
async def create_model(
    request: Request,
    actor: Actor,
    workspace_id: str,
    body: ModelConfigCreate,
) -> ModelConfigResource:
    return await _service(request).create(
        actor=actor,
        workspace_id=workspace_id,
        request=body,
    )


@router.get("/workspaces/{workspace_id}/models/{model_id}", response_model=ModelConfigResource)
async def get_model(request: Request, actor: Actor, workspace_id: str, model_id: str) -> ModelConfigResource:
    return await _service(request).get(actor=actor, workspace_id=workspace_id, model_id=model_id)


@router.patch("/workspaces/{workspace_id}/models/{model_id}", response_model=ModelConfigResource)
async def patch_model(
    request: Request,
    actor: Actor,
    workspace_id: str,
    model_id: str,
    body: ModelConfigPatch,
) -> ModelConfigResource:
    return await _service(request).patch(
        actor=actor,
        workspace_id=workspace_id,
        model_id=model_id,
        request=body,
    )


@router.post("/workspaces/{workspace_id}/models/test", response_model=ModelConnectionTestResult)
async def test_model_candidate(
    request: Request, actor: Actor, workspace_id: str, body: ModelConfigCreate
) -> ModelConnectionTestResult:
    return await _service(request).test_candidate(actor=actor, workspace_id=workspace_id, request=body)
