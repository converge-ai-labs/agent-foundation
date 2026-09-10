from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.mcp_authorization_launch import MCPAuthorizationLaunch
from ...models.mcp_connection_command_request import MCPConnectionCommandRequest
from ...types import Response


def build_request(
    connection_id: str,
    *,
    body: MCPConnectionCommandRequest,
    idempotency_key: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/mcp-connections/{connection_id}/authorize".format(
            connection_id=quote(str(connection_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | MCPAuthorizationLaunch:
    if response.status_code == 200:
        response_200 = MCPAuthorizationLaunch.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | MCPAuthorizationLaunch]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    connection_id: str,
    *,
    client: AuthenticatedClient,
    body: MCPConnectionCommandRequest,
    idempotency_key: str,
) -> Response[ErrorResponse | MCPAuthorizationLaunch]:
    """Authorize Mcp Connection

    Args:
        connection_id (str):
        idempotency_key (str):
        body (MCPConnectionCommandRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MCPAuthorizationLaunch]
    """

    kwargs = build_request(
        connection_id=connection_id,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    connection_id: str,
    *,
    client: AuthenticatedClient,
    body: MCPConnectionCommandRequest,
    idempotency_key: str,
) -> ErrorResponse | MCPAuthorizationLaunch | None:
    """Authorize Mcp Connection

    Args:
        connection_id (str):
        idempotency_key (str):
        body (MCPConnectionCommandRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MCPAuthorizationLaunch
    """

    return sync_detailed(
        connection_id=connection_id,
        client=client,
        body=body,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    connection_id: str,
    *,
    client: AuthenticatedClient,
    body: MCPConnectionCommandRequest,
    idempotency_key: str,
) -> Response[ErrorResponse | MCPAuthorizationLaunch]:
    """Authorize Mcp Connection

    Args:
        connection_id (str):
        idempotency_key (str):
        body (MCPConnectionCommandRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MCPAuthorizationLaunch]
    """

    kwargs = build_request(
        connection_id=connection_id,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    connection_id: str,
    *,
    client: AuthenticatedClient,
    body: MCPConnectionCommandRequest,
    idempotency_key: str,
) -> ErrorResponse | MCPAuthorizationLaunch | None:
    """Authorize Mcp Connection

    Args:
        connection_id (str):
        idempotency_key (str):
        body (MCPConnectionCommandRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MCPAuthorizationLaunch
    """

    return (
        await asyncio_detailed(
            connection_id=connection_id,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed
