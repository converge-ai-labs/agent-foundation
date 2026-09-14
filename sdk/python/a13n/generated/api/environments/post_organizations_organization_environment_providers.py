from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.create_provider_request import CreateProviderRequest
from ...models.environment_provider import EnvironmentProvider
from ...models.error_response import ErrorResponse
from ...types import Response


def build_request(
    organization: str,
    *,
    body: CreateProviderRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/organizations/{organization}/environment-providers".format(
            organization=quote(str(organization), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> EnvironmentProvider | ErrorResponse:
    if response.status_code == 201:
        response_201 = EnvironmentProvider.from_dict(response.json())

        return response_201

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[EnvironmentProvider | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    organization: str,
    *,
    client: AuthenticatedClient,
    body: CreateProviderRequest,
) -> Response[EnvironmentProvider | ErrorResponse]:
    """Organization Create Provider

    Args:
        organization (str):
        body (CreateProviderRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EnvironmentProvider | ErrorResponse]
    """

    kwargs = build_request(
        organization=organization,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    organization: str,
    *,
    client: AuthenticatedClient,
    body: CreateProviderRequest,
) -> EnvironmentProvider | ErrorResponse | None:
    """Organization Create Provider

    Args:
        organization (str):
        body (CreateProviderRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EnvironmentProvider | ErrorResponse
    """

    return sync_detailed(
        organization=organization,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    organization: str,
    *,
    client: AuthenticatedClient,
    body: CreateProviderRequest,
) -> Response[EnvironmentProvider | ErrorResponse]:
    """Organization Create Provider

    Args:
        organization (str):
        body (CreateProviderRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EnvironmentProvider | ErrorResponse]
    """

    kwargs = build_request(
        organization=organization,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization: str,
    *,
    client: AuthenticatedClient,
    body: CreateProviderRequest,
) -> EnvironmentProvider | ErrorResponse | None:
    """Organization Create Provider

    Args:
        organization (str):
        body (CreateProviderRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EnvironmentProvider | ErrorResponse
    """

    return (
        await asyncio_detailed(
            organization=organization,
            client=client,
            body=body,
        )
    ).parsed
