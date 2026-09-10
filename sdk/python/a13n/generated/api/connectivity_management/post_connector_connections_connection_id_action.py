from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.connector_connection import ConnectorConnection
from ...models.connector_connection_command_request import ConnectorConnectionCommandRequest
from ...models.error_response import ErrorResponse
from ...models.post_connector_connections_connection_id_action_action import (
    PostConnectorConnectionsConnectionIdActionAction,
)
from ...types import Response


def build_request(
    connection_id: str,
    action: PostConnectorConnectionsConnectionIdActionAction,
    *,
    body: ConnectorConnectionCommandRequest,
    idempotency_key: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/connector-connections/{connection_id}/{action}".format(
            connection_id=quote(str(connection_id), safe=""),
            action=quote(str(action), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ConnectorConnection | ErrorResponse:
    if response.status_code == 200:
        response_200 = ConnectorConnection.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ConnectorConnection | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    connection_id: str,
    action: PostConnectorConnectionsConnectionIdActionAction,
    *,
    client: AuthenticatedClient,
    body: ConnectorConnectionCommandRequest,
    idempotency_key: str,
) -> Response[ConnectorConnection | ErrorResponse]:
    """Change Connector Connection Lifecycle

    Args:
        connection_id (str):
        action (PostConnectorConnectionsConnectionIdActionAction):
        idempotency_key (str):
        body (ConnectorConnectionCommandRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConnectorConnection | ErrorResponse]
    """

    kwargs = build_request(
        connection_id=connection_id,
        action=action,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    connection_id: str,
    action: PostConnectorConnectionsConnectionIdActionAction,
    *,
    client: AuthenticatedClient,
    body: ConnectorConnectionCommandRequest,
    idempotency_key: str,
) -> ConnectorConnection | ErrorResponse | None:
    """Change Connector Connection Lifecycle

    Args:
        connection_id (str):
        action (PostConnectorConnectionsConnectionIdActionAction):
        idempotency_key (str):
        body (ConnectorConnectionCommandRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConnectorConnection | ErrorResponse
    """

    return sync_detailed(
        connection_id=connection_id,
        action=action,
        client=client,
        body=body,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    connection_id: str,
    action: PostConnectorConnectionsConnectionIdActionAction,
    *,
    client: AuthenticatedClient,
    body: ConnectorConnectionCommandRequest,
    idempotency_key: str,
) -> Response[ConnectorConnection | ErrorResponse]:
    """Change Connector Connection Lifecycle

    Args:
        connection_id (str):
        action (PostConnectorConnectionsConnectionIdActionAction):
        idempotency_key (str):
        body (ConnectorConnectionCommandRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConnectorConnection | ErrorResponse]
    """

    kwargs = build_request(
        connection_id=connection_id,
        action=action,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    connection_id: str,
    action: PostConnectorConnectionsConnectionIdActionAction,
    *,
    client: AuthenticatedClient,
    body: ConnectorConnectionCommandRequest,
    idempotency_key: str,
) -> ConnectorConnection | ErrorResponse | None:
    """Change Connector Connection Lifecycle

    Args:
        connection_id (str):
        action (PostConnectorConnectionsConnectionIdActionAction):
        idempotency_key (str):
        body (ConnectorConnectionCommandRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConnectorConnection | ErrorResponse
    """

    return (
        await asyncio_detailed(
            connection_id=connection_id,
            action=action,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed
