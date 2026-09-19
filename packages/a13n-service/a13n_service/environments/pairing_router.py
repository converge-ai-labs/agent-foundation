"""Narrow daemon enrollment and authenticated Workspace approval boundaries."""

from typing import Annotated

from a13n_environment.remote_envd.pairing import (
    PAIRING_PATH,
    PairingChallenge,
    PairingRequest,
    PairingResponse,
    credential_digest,
)
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import ValidationError

from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.iam.http.resource_dependencies import WorkspaceId
from a13n_service.request_runtime import get_control_runtime, get_process_runtime

from .domain import Environment
from .errors import connection_dependency_unavailable, invalid_environment
from .pairing import DevicePairingService

public_router = APIRouter(tags=["envd pairing"])
router = APIRouter()
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]
PairingId = Annotated[str, Path(pattern=r"^pair-[0-9a-f]{24}$")]


def _service(request: Request) -> DevicePairingService:
    control = get_control_runtime(request)
    runtime = get_process_runtime(request)
    if control is None or control.client_connections is None or runtime is None or runtime.status.draining:
        raise connection_dependency_unavailable()
    connections = control.client_connections.service
    return DevicePairingService(control.environments, connections.coordination, public_origin=connections.public_origin)


@public_router.post(PAIRING_PATH, response_model=PairingResponse)
async def pair(request: Request, response: Response) -> PairingResponse:
    # This credential deliberately bypasses broad Service IAM; it authorizes
    # neither resource reads nor approval. Bound the body before JSON parsing.
    values = request.headers.getlist("authorization")
    if len(values) != 1 or request.url.query:
        raise HTTPException(401, "An envd pairing Bearer credential is required")
    scheme, _, credential = values[0].partition(" ")
    try:
        if scheme.lower() != "bearer":
            raise ValueError("Invalid scheme")
        credential_digest(credential)
    except ValueError as error:
        raise HTTPException(401, "An envd pairing Bearer credential is required") from error
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 4096:
            raise HTTPException(413, "Pairing request is too large")
    try:
        body = PairingRequest.model_validate_json(data)
    except ValidationError as error:
        raise invalid_environment("Invalid Device pairing request") from error
    response.headers["Cache-Control"] = "no-store"
    return await _service(request).pair(credential, body)


@router.get("/workspaces/{workspace}/device-pairings/{pairing_id}")
async def inspect_pairing(
    request: Request, response: Response, actor: Actor, workspace_id: WorkspaceId, pairing_id: PairingId
) -> PairingChallenge:
    response.headers["Cache-Control"] = "no-store"
    return await _service(request).inspect(actor, workspace_id, pairing_id)


@router.post("/workspaces/{workspace}/device-pairings/{pairing_id}/approve")
async def approve_pairing(
    request: Request, actor: Actor, workspace_id: WorkspaceId, pairing_id: PairingId
) -> Environment:
    return await _service(request).approve(actor, workspace_id, pairing_id)


@router.post("/workspaces/{workspace}/device-pairings/{pairing_id}/reject", status_code=204)
async def reject_pairing(request: Request, actor: Actor, workspace_id: WorkspaceId, pairing_id: PairingId) -> None:
    await _service(request).reject(actor, workspace_id, pairing_id)


@router.post("/environments/{environment_id}/revoke-device")
async def revoke_device(request: Request, actor: Actor, environment_id: str) -> Environment:
    return await _service(request).revoke(actor, environment_id)
