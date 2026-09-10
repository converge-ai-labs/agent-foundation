from http import HTTPStatus
from typing import Any

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.mcp_connection import MCPConnection
from ...types import UNSET, Response


def build_request(
    *,
    code: str,
    state: str,
    iss: str,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["code"] = code

    params["state"] = state

    params["iss"] = iss

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/oauth/mcp/callback",
        "params": params,
    }

    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> ErrorResponse | MCPConnection:
    if response.status_code == 200:
        response_200 = MCPConnection.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | MCPConnection]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    code: str,
    state: str,
    iss: str,
) -> Response[ErrorResponse | MCPConnection]:
    """Mcp Oauth Callback

    Args:
        code (str):
        state (str):
        iss (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MCPConnection]
    """

    kwargs = build_request(
        code=code,
        state=state,
        iss=iss,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient,
    code: str,
    state: str,
    iss: str,
) -> ErrorResponse | MCPConnection | None:
    """Mcp Oauth Callback

    Args:
        code (str):
        state (str):
        iss (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MCPConnection
    """

    return sync_detailed(
        client=client,
        code=code,
        state=state,
        iss=iss,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    code: str,
    state: str,
    iss: str,
) -> Response[ErrorResponse | MCPConnection]:
    """Mcp Oauth Callback

    Args:
        code (str):
        state (str):
        iss (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MCPConnection]
    """

    kwargs = build_request(
        code=code,
        state=state,
        iss=iss,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    code: str,
    state: str,
    iss: str,
) -> ErrorResponse | MCPConnection | None:
    """Mcp Oauth Callback

    Args:
        code (str):
        state (str):
        iss (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MCPConnection
    """

    return (
        await asyncio_detailed(
            client=client,
            code=code,
            state=state,
            iss=iss,
        )
    ).parsed
