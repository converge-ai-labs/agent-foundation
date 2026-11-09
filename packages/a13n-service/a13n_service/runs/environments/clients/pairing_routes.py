"""Browser-authorized pairing and a separate native credential claim endpoint."""

import ipaddress

from fastapi import APIRouter, Request, Response

from a13n_service.infra.errors import ServiceError
from a13n_service.runs.environments.clients import pairing
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1/client-environments/pairings", tags=["client-environments"])


@router.post("", response_model=pairing.PairingCreated, openapi_extra={"x-a13n-mcp": False})
async def create_client_pairing(response: Response, actor: Actor, workspace_id: WorkspaceId, runtime: CurrentRuntime):
    response.headers["Cache-Control"] = "no-store"
    return await pairing.create(runtime, actor, workspace_id)


@router.get("/{pairing_id}", response_model=pairing.Pairing, openapi_extra={"x-a13n-mcp": False})
async def get_client_pairing(
    pairing_id: str, response: Response, actor: Actor, workspace_id: WorkspaceId, runtime: CurrentRuntime
):
    response.headers["Cache-Control"] = "no-store"
    return pairing.public((await pairing.owned(runtime, actor, workspace_id, pairing_id))[1])


@router.post("/{pairing_id}/approve", response_model=pairing.Pairing, openapi_extra={"x-a13n-mcp": False})
async def approve_client_pairing(
    pairing_id: str,
    body: pairing.PairingApproval,
    response: Response,
    actor: Actor,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
):
    response.headers["Cache-Control"] = "no-store"
    return await pairing.approve(runtime, actor, workspace_id, pairing_id, body.verification_code)


@router.post("/{pairing_id}/cancel", response_model=pairing.Pairing, openapi_extra={"x-a13n-mcp": False})
async def cancel_client_pairing(
    pairing_id: str, response: Response, actor: Actor, workspace_id: WorkspaceId, runtime: CurrentRuntime
):
    response.headers["Cache-Control"] = "no-store"
    return await pairing.cancel(runtime, actor, workspace_id, pairing_id)


@router.post("/{pairing_id}/claim", response_model=pairing.ClaimResult, openapi_extra={"x-a13n-mcp": False})
async def claim_client_pairing(
    pairing_id: str,
    body: pairing.DeviceClaim,
    request: Request,
    response: Response,
    runtime: CurrentRuntime,
):
    token = native_credential(request, runtime)
    response.headers["Cache-Control"] = "no-store"
    return await pairing.claim(runtime, pairing_id, token, body)


@router.post("/{pairing_id}/recognize", response_model=pairing.Pairing, openapi_extra={"x-a13n-mcp": False})
async def recognize_client_pairing(
    pairing_id: str,
    body: pairing.Recognition,
    request: Request,
    response: Response,
    runtime: CurrentRuntime,
):
    token = native_credential(request, runtime)
    response.headers["Cache-Control"] = "no-store"
    return await pairing.recognize(runtime, pairing_id, token, body)


def native_credential(request: Request, runtime: Runtime) -> str:
    values = request.headers.getlist("x-a13n-device-credential")
    scheme, _, token = values[0].partition(" ") if len(values) == 1 else ("", "", "")
    loopback = False
    if runtime.settings.client_environments.allow_loopback_ws and request.client:
        try:
            loopback = ipaddress.ip_address(request.client.host).is_loopback
        except ValueError:
            pass
    if (
        request.headers.get("origin") is not None
        or request.query_params
        or (request.url.scheme != "https" and not loopback)
        or "authorization" in request.headers
        or scheme.lower() != "bearer"
        or not 32 <= len(token) <= 4096
    ):
        raise ServiceError("unauthenticated", "A native device credential is required")
    return token
