"""Workspace models by key, the model catalog they start from and the workspace's media-understanding defaults."""

from fastapi import APIRouter, Request, Response

from a13n_service.infra.http import IfMatch, PageLimit, key_tagged, tagged
from a13n_service.resources.models import media, service
from a13n_service.resources.models.catalog import ModelsDevCatalog
from a13n_service.resources.models.schemas import (
    MediaDefaults,
    MediaUnderstandingSelection,
    Model,
    ModelCatalog,
    ModelCreate,
    ModelPage,
    ModelUpdate,
)
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1", tags=["models"])


@router.post("/models", response_model=Model, status_code=201, openapi_extra={"x-a13n-mcp": True})
async def create_model(
    response: Response, workspace_id: WorkspaceId, body: ModelCreate, actor: Actor, runtime: CurrentRuntime
) -> Model:
    """Needs `write` on the workspace and on the model's provider, whose credential the model spends."""
    result = await service.create_model(runtime.storage, actor, workspace_id, body, registry=runtime.registry)
    return key_tagged(response, result)


@router.get("/models", response_model=ModelPage, openapi_extra={"x-a13n-mcp": True})
async def list_models(
    workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime, limit: PageLimit = 50, cursor: str | None = None
) -> ModelPage:
    return await service.list_models(runtime.storage, actor, workspace_id, limit=limit, cursor=cursor)


@router.get("/models/{key}", response_model=Model, openapi_extra={"x-a13n-mcp": True})
async def get_model(
    response: Response, workspace_id: WorkspaceId, key: str, actor: Actor, runtime: CurrentRuntime
) -> Model:
    return key_tagged(response, await service.get_model(runtime.storage, actor, workspace_id, key))


@router.patch("/models/{key}", response_model=Model, openapi_extra={"x-a13n-mcp": True})
async def update_model(
    response: Response,
    workspace_id: WorkspaceId,
    key: str,
    body: ModelUpdate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Model:
    """A configuration change also needs `write` on the model's provider."""
    result = await service.update_model(
        runtime.storage, actor, workspace_id, key, body, if_match=if_match, registry=runtime.registry
    )
    return key_tagged(response, result)


@router.get("/model-catalog", response_model=ModelCatalog, openapi_extra={"x-a13n-mcp": True})
async def get_model_catalog(request: Request, actor: Actor) -> ModelCatalog:
    """The models.dev models the registered model provider types serve, for any signed-in principal."""
    catalog: ModelsDevCatalog = request.app.state.model_catalog
    return await catalog.read()


@router.get("/media-understanding-defaults", response_model=MediaDefaults, openapi_extra={"x-a13n-mcp": True})
async def get_media_defaults(
    response: Response, workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime
) -> MediaDefaults:
    return tagged(response, await media.get_media_defaults(runtime.storage, actor, workspace_id))


@router.put("/media-understanding-defaults", response_model=MediaDefaults, openapi_extra={"x-a13n-mcp": True})
async def replace_media_defaults(
    response: Response,
    workspace_id: WorkspaceId,
    body: MediaUnderstandingSelection,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> MediaDefaults:
    """Replaces all three kinds; each model must declare it understands its kind. Requires workspace admin."""
    replaced = await media.replace_media_defaults(
        runtime.storage, runtime.access, actor, workspace_id, body, if_match=if_match
    )
    return tagged(response, replaced)
