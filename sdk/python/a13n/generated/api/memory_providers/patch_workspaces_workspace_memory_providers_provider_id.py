from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.memory_provider import MemoryProvider
from ...models.update_memory_provider_request import UpdateMemoryProviderRequest
from ...types import Response


def build_request(
    workspace: str,
    provider_id: str,
    *,
    body: UpdateMemoryProviderRequest,
    if_match: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["If-Match"] = if_match

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/api/v1/workspaces/{workspace}/memory-providers/{provider_id}".format(
            workspace=quote(str(workspace), safe=""),
            provider_id=quote(str(provider_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | MemoryProvider:
    if response.status_code == 200:
        response_200 = MemoryProvider.from_dict(response.json())

        return response_200

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
    provider_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateMemoryProviderRequest,
    if_match: str,
) -> Response[ErrorResponse | MemoryProvider]:
    """Update Workspace Provider

    Args:
        workspace (str):
        provider_id (str):
        if_match (str):
        body (UpdateMemoryProviderRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MemoryProvider]
    """

    kwargs = build_request(
        workspace=workspace,
        provider_id=provider_id,
        body=body,
        if_match=if_match,
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
    body: UpdateMemoryProviderRequest,
    if_match: str,
) -> ErrorResponse | MemoryProvider | None:
    """Update Workspace Provider

    Args:
        workspace (str):
        provider_id (str):
        if_match (str):
        body (UpdateMemoryProviderRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MemoryProvider
    """

    return sync_detailed(
        workspace=workspace,
        provider_id=provider_id,
        client=client,
        body=body,
        if_match=if_match,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    provider_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateMemoryProviderRequest,
    if_match: str,
) -> Response[ErrorResponse | MemoryProvider]:
    """Update Workspace Provider

    Args:
        workspace (str):
        provider_id (str):
        if_match (str):
        body (UpdateMemoryProviderRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MemoryProvider]
    """

    kwargs = build_request(
        workspace=workspace,
        provider_id=provider_id,
        body=body,
        if_match=if_match,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    provider_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateMemoryProviderRequest,
    if_match: str,
) -> ErrorResponse | MemoryProvider | None:
    """Update Workspace Provider

    Args:
        workspace (str):
        provider_id (str):
        if_match (str):
        body (UpdateMemoryProviderRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MemoryProvider
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            provider_id=provider_id,
            client=client,
            body=body,
            if_match=if_match,
        )
    ).parsed
