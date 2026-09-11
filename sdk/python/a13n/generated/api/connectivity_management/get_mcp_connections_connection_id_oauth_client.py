from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.mcpo_auth_client_configuration import MCPOAuthClientConfiguration
from ...types import Response


def build_request(
    connection_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/mcp-connections/{connection_id}/oauth-client".format(
            connection_id=quote(str(connection_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | MCPOAuthClientConfiguration | None:
    if response.status_code == 200:

        def _parse_response_200(data: object) -> MCPOAuthClientConfiguration | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                response_200_type_0 = MCPOAuthClientConfiguration.from_dict(data)

                return response_200_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(MCPOAuthClientConfiguration | None, data)

        response_200 = _parse_response_200(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | MCPOAuthClientConfiguration | None]:
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
) -> Response[ErrorResponse | MCPOAuthClientConfiguration | None]:
    """Get Mcp Oauth Client

    Args:
        connection_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MCPOAuthClientConfiguration | None]
    """

    kwargs = build_request(
        connection_id=connection_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    connection_id: str,
    *,
    client: AuthenticatedClient,
) -> ErrorResponse | MCPOAuthClientConfiguration | None | None:
    """Get Mcp Oauth Client

    Args:
        connection_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MCPOAuthClientConfiguration | None
    """

    return sync_detailed(
        connection_id=connection_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    connection_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[ErrorResponse | MCPOAuthClientConfiguration | None]:
    """Get Mcp Oauth Client

    Args:
        connection_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MCPOAuthClientConfiguration | None]
    """

    kwargs = build_request(
        connection_id=connection_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    connection_id: str,
    *,
    client: AuthenticatedClient,
) -> ErrorResponse | MCPOAuthClientConfiguration | None | None:
    """Get Mcp Oauth Client

    Args:
        connection_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MCPOAuthClientConfiguration | None
    """

    return (
        await asyncio_detailed(
            connection_id=connection_id,
            client=client,
        )
    ).parsed
