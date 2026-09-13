from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.mcp_server import MCPServer
from ...types import Response


def build_request(
    server_key: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/mcp-servers/{server_key}".format(
            server_key=quote(str(server_key), safe=""),
        ),
    }

    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> ErrorResponse | MCPServer:
    if response.status_code == 200:
        response_200 = MCPServer.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | MCPServer]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    server_key: str,
    *,
    client: AuthenticatedClient,
) -> Response[ErrorResponse | MCPServer]:
    """Get Mcp Server

    Args:
        server_key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MCPServer]
    """

    kwargs = build_request(
        server_key=server_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    server_key: str,
    *,
    client: AuthenticatedClient,
) -> ErrorResponse | MCPServer | None:
    """Get Mcp Server

    Args:
        server_key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MCPServer
    """

    return sync_detailed(
        server_key=server_key,
        client=client,
    ).parsed


async def asyncio_detailed(
    server_key: str,
    *,
    client: AuthenticatedClient,
) -> Response[ErrorResponse | MCPServer]:
    """Get Mcp Server

    Args:
        server_key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MCPServer]
    """

    kwargs = build_request(
        server_key=server_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    server_key: str,
    *,
    client: AuthenticatedClient,
) -> ErrorResponse | MCPServer | None:
    """Get Mcp Server

    Args:
        server_key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MCPServer
    """

    return (
        await asyncio_detailed(
            server_key=server_key,
            client=client,
        )
    ).parsed
