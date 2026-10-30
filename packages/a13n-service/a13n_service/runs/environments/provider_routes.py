"""Environment connection lifecycle checks spanning templates and retained instances."""

from fastapi import APIRouter, Response

from a13n_service.infra.http import IfMatch
from a13n_service.resources.providers.schemas import EnvironmentProviderReferences
from a13n_service.runs.environments import provider_connections
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1", tags=["providers"])


@router.get(
    "/environment-providers/{provider_id}/references",
    response_model=EnvironmentProviderReferences,
    openapi_extra={"x-a13n-mcp": True},
)
async def environment_provider_references(
    workspace_id: WorkspaceId, provider_id: str, actor: Actor, runtime: CurrentRuntime
) -> EnvironmentProviderReferences:
    return await provider_connections.references(runtime.storage, actor, workspace_id, provider_id)


@router.delete("/environment-providers/{provider_id}", status_code=204, openapi_extra={"x-a13n-mcp": True})
async def delete_environment_provider(
    workspace_id: WorkspaceId, provider_id: str, actor: Actor, runtime: CurrentRuntime, if_match: IfMatch = None
) -> Response:
    await provider_connections.delete(runtime.storage, actor, workspace_id, provider_id, if_match=if_match)
    return Response(status_code=204)
