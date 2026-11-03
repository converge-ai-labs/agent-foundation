"""Client registration and explicit administration; credentials never enter URLs or cacheable responses."""

from fastapi import APIRouter, Response

from a13n_service.infra.http import IfMatch, tagged
from a13n_service.runs.environments.clients import service
from a13n_service.runs.environments.clients.schemas import ClientEnvironment, Registered, Registration, StatusChange
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1/client-environments", tags=["client-environments"])


@router.post(
    "",
    response_model=Registered,
    status_code=201,
    responses={200: {"model": Registered, "description": "Existing registration; no token is returned"}},
    openapi_extra={"x-a13n-mcp": False},
)
async def register_client_environment(
    response: Response,
    workspace_id: WorkspaceId,
    body: Registration,
    actor: Actor,
    runtime: CurrentRuntime,
) -> Registered:
    result = await service.register(runtime, actor, workspace_id, body)
    tagged(response, result.client.environment)
    response.headers["cache-control"] = "no-store"
    if result.token is None:
        response.status_code = 200
    return result


@router.get("/{environment_id}", response_model=ClientEnvironment, openapi_extra={"x-a13n-mcp": True})
async def get_client_environment(
    response: Response,
    workspace_id: WorkspaceId,
    environment_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
) -> ClientEnvironment:
    result = await service.read(runtime, actor, workspace_id, environment_id)
    tagged(response, result.environment)
    response.headers["cache-control"] = "no-store"
    return result


@router.patch("/{environment_id}/status", response_model=ClientEnvironment, openapi_extra={"x-a13n-mcp": False})
async def change_client_environment_status(
    response: Response,
    workspace_id: WorkspaceId,
    environment_id: str,
    body: StatusChange,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> ClientEnvironment:
    result = await service.change_status(runtime, actor, workspace_id, environment_id, body, if_match=if_match)
    tagged(response, result.environment)
    return result


@router.post("/{environment_id}/rotate-credential", response_model=Registered, openapi_extra={"x-a13n-mcp": False})
async def rotate_client_environment_credential(
    response: Response,
    workspace_id: WorkspaceId,
    environment_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Registered:
    result = await service.credential(runtime, actor, workspace_id, environment_id, rotate=True, if_match=if_match)
    tagged(response, result.client.environment)
    response.headers["cache-control"] = "no-store"
    return result


@router.post("/{environment_id}/revoke-credential", response_model=Registered, openapi_extra={"x-a13n-mcp": False})
async def revoke_client_environment_credential(
    response: Response,
    workspace_id: WorkspaceId,
    environment_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Registered:
    result = await service.credential(runtime, actor, workspace_id, environment_id, rotate=False, if_match=if_match)
    tagged(response, result.client.environment)
    response.headers["cache-control"] = "no-store"
    return result
