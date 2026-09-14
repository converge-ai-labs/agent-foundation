from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.search_provider import SearchProvider
from ...models.update_search_provider_request import UpdateSearchProviderRequest
from ...types import Response


def build_request(
    organization: str,
    provider_id: str,
    *,
    body: UpdateSearchProviderRequest,
    if_match: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["If-Match"] = if_match

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/api/v1/organizations/{organization}/search-providers/{provider_id}".format(
            organization=quote(str(organization), safe=""),
            provider_id=quote(str(provider_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
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
    organization: str,
    provider_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateSearchProviderRequest,
    if_match: str,
) -> Response[ErrorResponse | SearchProvider]:
    """Update Organization Provider

    Args:
        organization (str):
        provider_id (str):
        if_match (str):
        body (UpdateSearchProviderRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SearchProvider]
    """

    kwargs = build_request(
        organization=organization,
        provider_id=provider_id,
        body=body,
        if_match=if_match,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    organization: str,
    provider_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateSearchProviderRequest,
    if_match: str,
) -> ErrorResponse | SearchProvider | None:
    """Update Organization Provider

    Args:
        organization (str):
        provider_id (str):
        if_match (str):
        body (UpdateSearchProviderRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SearchProvider
    """

    return sync_detailed(
        organization=organization,
        provider_id=provider_id,
        client=client,
        body=body,
        if_match=if_match,
    ).parsed


async def asyncio_detailed(
    organization: str,
    provider_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateSearchProviderRequest,
    if_match: str,
) -> Response[ErrorResponse | SearchProvider]:
    """Update Organization Provider

    Args:
        organization (str):
        provider_id (str):
        if_match (str):
        body (UpdateSearchProviderRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SearchProvider]
    """

    kwargs = build_request(
        organization=organization,
        provider_id=provider_id,
        body=body,
        if_match=if_match,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization: str,
    provider_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateSearchProviderRequest,
    if_match: str,
) -> ErrorResponse | SearchProvider | None:
    """Update Organization Provider

    Args:
        organization (str):
        provider_id (str):
        if_match (str):
        body (UpdateSearchProviderRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SearchProvider
    """

    return (
        await asyncio_detailed(
            organization=organization,
            provider_id=provider_id,
            client=client,
            body=body,
            if_match=if_match,
        )
    ).parsed
