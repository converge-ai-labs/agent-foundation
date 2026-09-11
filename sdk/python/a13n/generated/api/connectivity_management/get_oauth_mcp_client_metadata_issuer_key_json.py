from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.mcp_client_metadata import MCPClientMetadata
from ...types import Response


def build_request(
    issuer_key: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/oauth/mcp/client-metadata/{issuer_key}.json".format(
            issuer_key=quote(str(issuer_key), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | MCPClientMetadata:
    if response.status_code == 200:
        response_200 = MCPClientMetadata.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | MCPClientMetadata]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    issuer_key: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | MCPClientMetadata]:
    """Mcp Client Metadata

    Args:
        issuer_key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MCPClientMetadata]
    """

    kwargs = build_request(
        issuer_key=issuer_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    issuer_key: str,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | MCPClientMetadata | None:
    """Mcp Client Metadata

    Args:
        issuer_key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MCPClientMetadata
    """

    return sync_detailed(
        issuer_key=issuer_key,
        client=client,
    ).parsed


async def asyncio_detailed(
    issuer_key: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | MCPClientMetadata]:
    """Mcp Client Metadata

    Args:
        issuer_key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MCPClientMetadata]
    """

    kwargs = build_request(
        issuer_key=issuer_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    issuer_key: str,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | MCPClientMetadata | None:
    """Mcp Client Metadata

    Args:
        issuer_key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MCPClientMetadata
    """

    return (
        await asyncio_detailed(
            issuer_key=issuer_key,
            client=client,
        )
    ).parsed
