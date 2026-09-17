"""Authenticated ticket management and ticket-only reverse EIP ingress."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, WebSocket

from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.request_runtime import get_control_runtime, get_process_runtime

from .service import ClientConnectionStatus, ClientConnectionTicket, connection_dependency_unavailable

router = APIRouter()
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]


@router.post("/environments/{environment_id}/connection-tickets", status_code=201)
async def issue_ticket(
    request: Request, response: Response, actor: Actor, environment_id: str
) -> ClientConnectionTicket:
    control = get_control_runtime(request)
    runtime = get_process_runtime(request)
    if control is None or control.client_connections is None or runtime is None or runtime.status.draining:
        raise connection_dependency_unavailable()
    response.headers["Cache-Control"] = "no-store"
    return await control.client_connections.service.issue_ticket(actor, environment_id)


@router.get("/environments/{environment_id}/connection")
async def connection_status(
    request: Request, response: Response, actor: Actor, environment_id: str
) -> ClientConnectionStatus:
    control = get_control_runtime(request)
    if control is None or control.client_connections is None:
        raise connection_dependency_unavailable()
    response.headers["Cache-Control"] = "no-store"
    return await control.client_connections.service.status(actor, environment_id)


@router.websocket("/environments/{environment_id}/connect")
async def connect(websocket: WebSocket, environment_id: str) -> None:
    control = get_control_runtime(websocket)
    if control is None or control.client_connections is None:
        await websocket.close(code=1013)
        return
    await control.client_connections.host.serve(websocket, environment_id)
