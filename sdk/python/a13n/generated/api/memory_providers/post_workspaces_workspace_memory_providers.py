from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.create_memory_provider_request import CreateMemoryProviderRequest
from ...models.error_response import ErrorResponse
from ...models.memory_provider import MemoryProvider
from ...types import Response


def build_request(
    workspace: str,
    *,
    body: CreateMemoryProviderRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/workspaces/{workspace}/memory-providers".format(
            workspace=quote(str(workspace), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | MemoryProvider:
    if response.status_code == 201:
        response_201 = MemoryProvider.from_dict(response.json())

        return response_201

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | MemoryProvider]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: CreateMemoryProviderRequest,
) -> Response[ErrorResponse | MemoryProvider]:
    """Create Workspace Provider

    Args:
        workspace (str):
        body (CreateMemoryProviderRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MemoryProvider]
    """

    kwargs = build_request(
        workspace=workspace,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: CreateMemoryProviderRequest,
) -> ErrorResponse | MemoryProvider | None:
    """Create Workspace Provider

    Args:
        workspace (str):
        body (CreateMemoryProviderRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MemoryProvider
    """

    return sync_detailed(
        workspace=workspace,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: CreateMemoryProviderRequest,
) -> Response[ErrorResponse | MemoryProvider]:
    """Create Workspace Provider

    Args:
        workspace (str):
        body (CreateMemoryProviderRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MemoryProvider]
    """

    kwargs = build_request(
        workspace=workspace,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: CreateMemoryProviderRequest,
) -> ErrorResponse | MemoryProvider | None:
    """Create Workspace Provider

    Args:
        workspace (str):
        body (CreateMemoryProviderRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MemoryProvider
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            client=client,
            body=body,
        )
    ).parsed
