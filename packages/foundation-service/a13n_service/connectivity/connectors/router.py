"""Connector management and verified setup callback routes."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from a13n_service.etags import resource_etag
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.request_runtime import get_connectivity_control_runtime

from .connections import ConnectorConnectionService
from .domain import (
    Connector,
    ConnectorCollection,
    ConnectorCommandRequest,
    ConnectorConnection,
    ConnectorConnectionCollection,
    ConnectorConnectionCommandRequest,
    ConnectorOperationReceipt,
    ConnectorSetupLaunch,
    ConnectorStatus,
    ConnectorTestResult,
    CreateConnectorConnectionRequest,
    CreateConnectorRequest,
    ReconnectConnectorConnectionRequest,
    ReplaceConnectorCredentialsRequest,
    StartConnectorConnectionSetupRequest,
    UpdateConnectorConnectionRequest,
    UpdateConnectorRequest,
)
from .errors import ConnectorError
from .service import ConnectorService

router = APIRouter(tags=["connectivity-management"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=512)]


def _connectors(request: Request) -> ConnectorService:
    runtime = get_connectivity_control_runtime(request)
    if runtime is None:
        raise ConnectorError(
            "connector_management_unavailable",
            "Connector Management is unavailable.",
            status_code=503,
        )
    return runtime.connectors


def _connections(request: Request) -> ConnectorConnectionService:
    runtime = get_connectivity_control_runtime(request)
    if runtime is None:
        raise ConnectorError(
            "connector_management_unavailable",
            "Connector Management is unavailable.",
            status_code=503,
        )
    return runtime.connector_connections


def _etag(response: Response, resource: Connector | ConnectorConnection) -> None:
    response.headers["ETag"] = resource_etag(resource.id, resource.updated_at)


@router.post(
    "/api/v1/workspaces/{workspace_id}/connectors",
    response_model=Connector,
    status_code=status.HTTP_201_CREATED,
)
async def create_connector(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
    body: CreateConnectorRequest,
    idempotency_key: IdempotencyKey,
) -> Connector:
    resource = await _connectors(request).create(
        actor=actor,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        request=body,
    )
    _etag(response, resource)
    return resource


@router.get("/api/v1/workspaces/{workspace_id}/connectors", response_model=ConnectorCollection)
async def list_connectors(
    request: Request,
    actor: Actor,
    workspace_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> ConnectorCollection:
    return await _connectors(request).list(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/api/v1/connectors/{connector_id}", response_model=Connector)
async def get_connector(
    request: Request,
    response: Response,
    actor: Actor,
    connector_id: str,
) -> Connector:
    resource = await _connectors(request).get(actor=actor, connector_id=connector_id)
    _etag(response, resource)
    return resource


@router.patch("/api/v1/connectors/{connector_id}", response_model=Connector)
async def update_connector(
    request: Request,
    response: Response,
    actor: Actor,
    connector_id: str,
    body: UpdateConnectorRequest,
) -> Connector:
    resource = await _connectors(request).update(actor=actor, connector_id=connector_id, request=body)
    _etag(response, resource)
    return resource


@router.post("/api/v1/connectors/{connector_id}/credentials", response_model=Connector)
async def replace_connector_credentials(
    request: Request,
    response: Response,
    actor: Actor,
    connector_id: str,
    body: ReplaceConnectorCredentialsRequest,
    idempotency_key: IdempotencyKey,
) -> Connector:
    resource = await _connectors(request).replace_credentials(
        actor=actor,
        connector_id=connector_id,
        idempotency_key=idempotency_key,
        request=body,
    )
    _etag(response, resource)
    return resource


@router.post("/api/v1/connectors/{connector_id}/test", response_model=ConnectorTestResult)
async def test_connector(
    request: Request,
    actor: Actor,
    connector_id: str,
    body: ConnectorCommandRequest,
    idempotency_key: IdempotencyKey,
) -> ConnectorTestResult:
    return await _connectors(request).test(
        actor=actor,
        connector_id=connector_id,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
    )


@router.post("/api/v1/connectors/{connector_id}/{action}", response_model=Connector)
async def change_connector_lifecycle(
    request: Request,
    response: Response,
    actor: Actor,
    connector_id: str,
    action: Literal["enable", "disable"],
    body: ConnectorCommandRequest,
    idempotency_key: IdempotencyKey,
) -> Connector:
    resource = await _connectors(request).set_status(
        actor=actor,
        connector_id=connector_id,
        status=ConnectorStatus.active if action == "enable" else ConnectorStatus.disabled,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
    )
    _etag(response, resource)
    return resource


@router.post(
    "/api/v1/workspaces/{workspace_id}/connector-connections",
    response_model=ConnectorConnection,
    status_code=status.HTTP_201_CREATED,
)
async def create_connector_connection(
    request: Request,
    actor: Actor,
    workspace_id: str,
    body: CreateConnectorConnectionRequest,
    idempotency_key: IdempotencyKey,
) -> ConnectorConnection:
    return await _connections(request).create(
        actor=actor,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        request=body,
    )


@router.post(
    "/api/v1/connector-connections/{connection_id}/setup",
    response_model=ConnectorSetupLaunch,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_connector_connection_setup(
    request: Request,
    actor: Actor,
    connection_id: str,
    body: StartConnectorConnectionSetupRequest,
    idempotency_key: IdempotencyKey,
) -> ConnectorSetupLaunch:
    return await _connections(request).start_setup(
        actor=actor,
        connection_id=connection_id,
        idempotency_key=idempotency_key,
        expected_version=body.expected_version,
        setup=body.setup,
        return_path=body.return_path,
    )


@router.get(
    "/api/v1/workspaces/{workspace_id}/connector-connections",
    response_model=ConnectorConnectionCollection,
)
async def list_connector_connections(
    request: Request,
    actor: Actor,
    workspace_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> ConnectorConnectionCollection:
    return await _connections(request).list(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/api/v1/connector-connections/{connection_id}", response_model=ConnectorConnection)
async def get_connector_connection(
    request: Request,
    response: Response,
    actor: Actor,
    connection_id: str,
) -> ConnectorConnection:
    resource = await _connections(request).get(actor=actor, connection_id=connection_id)
    _etag(response, resource)
    return resource


@router.patch("/api/v1/connector-connections/{connection_id}", response_model=ConnectorConnection)
async def update_connector_connection(
    request: Request,
    response: Response,
    actor: Actor,
    connection_id: str,
    body: UpdateConnectorConnectionRequest,
) -> ConnectorConnection:
    resource = await _connections(request).update(
        actor=actor,
        connection_id=connection_id,
        request=body,
    )
    _etag(response, resource)
    return resource


@router.post(
    "/api/v1/connector-connections/{connection_id}/{action}",
    response_model=ConnectorConnection,
)
async def change_connector_connection_lifecycle(
    request: Request,
    response: Response,
    actor: Actor,
    connection_id: str,
    action: Literal["enable", "disable"],
    body: ConnectorConnectionCommandRequest,
    idempotency_key: IdempotencyKey,
) -> ConnectorConnection:
    resource = await _connections(request).set_enabled(
        actor=actor,
        connection_id=connection_id,
        enabled=action == "enable",
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
    )
    _etag(response, resource)
    return resource


@router.post(
    "/api/v1/connector-connections/{connection_id}/reconnect",
    response_model=ConnectorSetupLaunch,
)
async def reconnect_connector_connection(
    request: Request,
    actor: Actor,
    connection_id: str,
    body: ReconnectConnectorConnectionRequest,
    idempotency_key: IdempotencyKey,
) -> ConnectorSetupLaunch:
    return await _connections(request).reconnect(
        actor=actor,
        connection_id=connection_id,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
        setup=body.setup,
        return_path=body.return_path,
    )


@router.post(
    "/api/v1/connector-connections/{connection_id}/revoke",
    response_model=ConnectorOperationReceipt,
    status_code=status.HTTP_202_ACCEPTED,
)
async def revoke_connector_connection(
    request: Request,
    actor: Actor,
    connection_id: str,
    body: ConnectorConnectionCommandRequest,
    idempotency_key: IdempotencyKey,
) -> ConnectorOperationReceipt:
    return await _connections(request).revoke(
        actor=actor,
        connection_id=connection_id,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
    )


@router.delete(
    "/api/v1/connector-connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_connector_connection(
    request: Request,
    actor: Actor,
    connection_id: str,
    expected_version: Annotated[int, Query(ge=1)],
    idempotency_key: IdempotencyKey,
) -> Response:
    await _connections(request).delete(
        actor=actor,
        connection_id=connection_id,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/connectivity/v1/connector-setup/callback", include_in_schema=False)
async def connector_setup_callback(
    request: Request,
    actor: Actor,
    session_uri: Annotated[str, Query(min_length=1, max_length=4096)],
) -> RedirectResponse:
    return_path = await _connections(request).complete_callback(actor=actor, session_uri=session_uri)
    runtime = get_connectivity_control_runtime(request)
    if runtime is None:
        raise ConnectorError("callback_unavailable", "Connector callback is unavailable.", status_code=503)
    return RedirectResponse(f"{runtime.public_origin.rstrip('/')}{return_path}", status_code=303)
