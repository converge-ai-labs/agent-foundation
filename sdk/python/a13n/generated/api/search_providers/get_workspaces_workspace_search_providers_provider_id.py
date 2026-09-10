from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.search_provider import SearchProvider
from ...types import Response


def build_request(
    workspace: str,
    provider_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/workspaces/{workspace}/search-providers/{provider_id}".format(
            workspace=quote(str(workspace), safe=""),
            provider_id=quote(str(provider_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SearchProvider:
    if response.status_code == 200:
        response_200 = SearchProvider.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | SearchProvider]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    workspace: str,
    provider_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[ErrorResponse | SearchProvider]:
    """Get Workspace Provider

    Args:
        workspace (str):
        provider_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SearchProvider]
    """

    kwargs = build_request(
        workspace=workspace,
        provider_id=provider_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    workspace: str,
    provider_id: str,
    *,
    client: AuthenticatedClient,
) -> ErrorResponse | SearchProvider | None:
    """Get Workspace Provider

    Args:
        workspace (str):
        provider_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SearchProvider
    """

    return sync_detailed(
        workspace=workspace,
        provider_id=provider_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    provider_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[ErrorResponse | SearchProvider]:
    """Get Workspace Provider

    Args:
        workspace (str):
        provider_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SearchProvider]
    """

    kwargs = build_request(
        workspace=workspace,
        provider_id=provider_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    provider_id: str,
    *,
    client: AuthenticatedClient,
) -> ErrorResponse | SearchProvider | None:
    """Get Workspace Provider

    Args:
        workspace (str):
        provider_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SearchProvider
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            provider_id=provider_id,
            client=client,
        )
    ).parsed
