from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.search_configuration import SearchConfiguration
from ...models.search_provider_test_result import SearchProviderTestResult
from ...types import Response


def build_request(
    organization: str,
    provider_id: str,
    *,
    body: SearchConfiguration,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/organizations/{organization}/search-providers/{provider_id}/test".format(
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
) -> ErrorResponse | SearchProviderTestResult:
    if response.status_code == 200:
        response_200 = SearchProviderTestResult.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | SearchProviderTestResult]:
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
    body: SearchConfiguration,
) -> Response[ErrorResponse | SearchProviderTestResult]:
    """Test Organization Provider

    Args:
        organization (str):
        provider_id (str):
        body (SearchConfiguration):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SearchProviderTestResult]
    """

    kwargs = build_request(
        organization=organization,
        provider_id=provider_id,
        body=body,
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
    body: SearchConfiguration,
) -> ErrorResponse | SearchProviderTestResult | None:
    """Test Organization Provider

    Args:
        organization (str):
        provider_id (str):
        body (SearchConfiguration):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SearchProviderTestResult
    """

    return sync_detailed(
        organization=organization,
        provider_id=provider_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    organization: str,
    provider_id: str,
    *,
    client: AuthenticatedClient,
    body: SearchConfiguration,
) -> Response[ErrorResponse | SearchProviderTestResult]:
    """Test Organization Provider

    Args:
        organization (str):
        provider_id (str):
        body (SearchConfiguration):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SearchProviderTestResult]
    """

    kwargs = build_request(
        organization=organization,
        provider_id=provider_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization: str,
    provider_id: str,
    *,
    client: AuthenticatedClient,
    body: SearchConfiguration,
) -> ErrorResponse | SearchProviderTestResult | None:
    """Test Organization Provider

    Args:
        organization (str):
        provider_id (str):
        body (SearchConfiguration):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SearchProviderTestResult
    """

    return (
        await asyncio_detailed(
            organization=organization,
            provider_id=provider_id,
            client=client,
            body=body,
        )
    ).parsed
