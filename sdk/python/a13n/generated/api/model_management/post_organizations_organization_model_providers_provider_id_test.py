from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.model_connection_test_result import ModelConnectionTestResult
from ...types import Response


def build_request(
    organization: str,
    provider_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/organizations/{organization}/model-providers/{provider_id}/test".format(
            organization=quote(str(organization), safe=""),
            provider_id=quote(str(provider_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ModelConnectionTestResult:
    if response.status_code == 200:
        response_200 = ModelConnectionTestResult.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | ModelConnectionTestResult]:
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
) -> Response[ErrorResponse | ModelConnectionTestResult]:
    """Organization Test Model Provider

    Args:
        organization (str):
        provider_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ModelConnectionTestResult]
    """

    kwargs = build_request(
        organization=organization,
        provider_id=provider_id,
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
) -> ErrorResponse | ModelConnectionTestResult | None:
    """Organization Test Model Provider

    Args:
        organization (str):
        provider_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ModelConnectionTestResult
    """

    return sync_detailed(
        organization=organization,
        provider_id=provider_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    organization: str,
    provider_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[ErrorResponse | ModelConnectionTestResult]:
    """Organization Test Model Provider

    Args:
        organization (str):
        provider_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ModelConnectionTestResult]
    """

    kwargs = build_request(
        organization=organization,
        provider_id=provider_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization: str,
    provider_id: str,
    *,
    client: AuthenticatedClient,
) -> ErrorResponse | ModelConnectionTestResult | None:
    """Organization Test Model Provider

    Args:
        organization (str):
        provider_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ModelConnectionTestResult
    """

    return (
        await asyncio_detailed(
            organization=organization,
            provider_id=provider_id,
            client=client,
        )
    ).parsed
