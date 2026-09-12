"""Public connection lifecycle and authorization operations."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.cleanup import ConnectionCleanupReceipt
from a13n_service.connectivity.connections.domain import Connection
from a13n_service.etags import resource_etag
from a13n_service.http_types import IdempotencyKey
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.iam.http.authentication import authenticate_mutation
from a13n_service.iam.http.resource_dependencies import WorkspaceId
from a13n_service.request_runtime import get_connectivity_control_runtime

from .access import ConnectionError
from .domain import (
    Authorization,
    AuthorizationRedirect,
    CompleteAuthorizationRequest,
    ConnectionCollection,
    ConnectionCommandRequest,
    CreateAuthorizationRequest,
    CreateConnectionRequest,
    LaunchAuthorizationRequest,
    ReceiveAuthorizationRequest,
    UpdateConnectionRequest,
)


def _private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


router = APIRouter(tags=["connections"], dependencies=[Depends(_private_response)])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]
MutationActor = Annotated[AuthenticatedActor, Depends(authenticate_mutation)]


def _runtime(request: Request):
    runtime = get_connectivity_control_runtime(request)
    if runtime is None:
        raise ConnectionError(
            "connection_management_unavailable",
            "Connection management is unavailable.",
            category=ErrorCategory.unavailable,
        )
    return runtime


def _etag(response: Response, resource: Connection) -> Connection:
    response.headers["ETag"] = resource_etag(resource.id, resource.updated_at)
    return resource


@router.post("/api/v1/workspaces/{workspace}/connections", response_model=Connection, status_code=201)
async def create_connection(
    request: Request,
    response: Response,
    actor: MutationActor,
    workspace_id: WorkspaceId,
    body: CreateConnectionRequest,
    idempotency_key: IdempotencyKey,
) -> Connection:
    return _etag(
        response,
        await _runtime(request).connections.create(
            actor=actor, workspace_id=workspace_id, idempotency_key=idempotency_key, request=body
        ),
    )


@router.get("/api/v1/workspaces/{workspace}/connections", response_model=ConnectionCollection)
async def list_connections(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> ConnectionCollection:
    return await _runtime(request).connections.list(actor=actor, workspace_id=workspace_id, limit=limit, cursor=cursor)


@router.get("/api/v1/connections/{connection_id}", response_model=Connection)
async def get_connection(request: Request, response: Response, actor: Actor, connection_id: str) -> Connection:
    return _etag(response, await _runtime(request).connections.get(actor=actor, connection_id=connection_id))


@router.patch("/api/v1/connections/{connection_id}", response_model=Connection)
async def update_connection(
    request: Request, response: Response, actor: MutationActor, connection_id: str, body: UpdateConnectionRequest
) -> Connection:
    return _etag(
        response, await _runtime(request).connections.update(actor=actor, connection_id=connection_id, request=body)
    )


@router.post("/api/v1/connections/{connection_id}/authorizations", response_model=Authorization, status_code=201)
async def create_connection_authorization(
    request: Request,
    actor: MutationActor,
    connection_id: str,
    body: CreateAuthorizationRequest,
    idempotency_key: IdempotencyKey,
) -> Authorization:
    return await _runtime(request).authorizations.create(
        actor=actor, connection_id=connection_id, idempotency_key=idempotency_key, request=body
    )


@router.get("/api/v1/connection-authorizations/{authorization_id}", response_model=Authorization)
async def get_connection_authorization(request: Request, actor: Actor, authorization_id: str) -> Authorization:
    return await _runtime(request).authorizations.get(actor=actor, authorization_id=authorization_id)


@router.post("/api/v1/connection-authorizations/{authorization_id}/cancel", response_model=Authorization)
async def cancel_connection_authorization(
    request: Request, actor: MutationActor, authorization_id: str
) -> Authorization:
    return await _runtime(request).authorizations.cancel(actor=actor, authorization_id=authorization_id)


@router.post("/api/v1/connection-authorizations/{authorization_id}/complete", response_model=Authorization)
async def complete_connection_authorization(
    request: Request, actor: MutationActor, authorization_id: str, body: CompleteAuthorizationRequest
) -> Authorization:
    return await _runtime(request).authorizations.complete(actor=actor, authorization_id=authorization_id, request=body)


@router.post("/api/v1/connection-authorizations/{authorization_id}/launch", response_model=AuthorizationRedirect)
async def launch_connection_authorization(
    request: Request, authorization_id: str, body: LaunchAuthorizationRequest
) -> AuthorizationRedirect:
    return await _runtime(request).authorizations.launch(authorization_id, body)


@router.post("/api/v1/connection-authorizations/{authorization_id}/receive", response_model=AuthorizationRedirect)
async def receive_connection_authorization(
    request: Request, authorization_id: str, body: ReceiveAuthorizationRequest
) -> AuthorizationRedirect:
    return await _runtime(request).authorizations.receive(authorization_id, body)


@router.post("/api/v1/connections/{connection_id}/check", response_model=Connection)
async def check_connection(
    request: Request, response: Response, actor: MutationActor, connection_id: str, body: ConnectionCommandRequest
) -> Connection:
    return _etag(
        response,
        await _runtime(request).checks.check(
            actor=actor, connection_id=connection_id, expected_version=body.expected_version
        ),
    )


@router.post("/api/v1/connections/{connection_id}/enable", response_model=Connection)
async def enable_connection(
    request: Request,
    response: Response,
    actor: MutationActor,
    connection_id: str,
    body: ConnectionCommandRequest,
    idempotency_key: IdempotencyKey,
) -> Connection:
    return _etag(
        response,
        await _runtime(request).connections.set_enabled(
            actor=actor,
            connection_id=connection_id,
            expected_version=body.expected_version,
            enabled=True,
            idempotency_key=idempotency_key,
        ),
    )


@router.post("/api/v1/connections/{connection_id}/disable", response_model=Connection)
async def disable_connection(
    request: Request,
    response: Response,
    actor: MutationActor,
    connection_id: str,
    body: ConnectionCommandRequest,
    idempotency_key: IdempotencyKey,
) -> Connection:
    return _etag(
        response,
        await _runtime(request).connections.set_enabled(
            actor=actor,
            connection_id=connection_id,
            expected_version=body.expected_version,
            enabled=False,
            idempotency_key=idempotency_key,
        ),
    )


@router.post("/api/v1/connections/{connection_id}/connector/revoke", response_model=ConnectionCleanupReceipt)
async def revoke_connector_authorization(
    request: Request,
    actor: MutationActor,
    connection_id: str,
    body: ConnectionCommandRequest,
    idempotency_key: IdempotencyKey,
) -> ConnectionCleanupReceipt:
    return await _runtime(request).connector_connections.revoke(
        actor=actor,
        connection_id=connection_id,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
    )


@router.delete("/api/v1/connections/{connection_id}", response_model=ConnectionCleanupReceipt)
async def delete_connection(
    request: Request,
    actor: MutationActor,
    connection_id: str,
    expected_version: Annotated[int, Query(ge=1)],
    idempotency_key: IdempotencyKey,
) -> ConnectionCleanupReceipt:
    runtime = _runtime(request)
    resource = await runtime.connections.get(actor=actor, connection_id=connection_id)
    service = runtime.connector_connections if resource.source.kind == "connector" else runtime.mcp_connections
    return await service.delete(
        actor=actor, connection_id=connection_id, expected_version=expected_version, idempotency_key=idempotency_key
    )
