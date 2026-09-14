from http import HTTPStatus
from typing import Any

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.get_auth_csrf_response_browser_proof_api_v1_auth_csrf_get import (
    GetAuthCsrfResponseBrowserProofApiV1AuthCsrfGet,
)
from ...types import Response


def build_request() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/auth/csrf",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | GetAuthCsrfResponseBrowserProofApiV1AuthCsrfGet:
    if response.status_code == 200:
        response_200 = GetAuthCsrfResponseBrowserProofApiV1AuthCsrfGet.from_dict(response.json())

        return response_200

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | GetAuthCsrfResponseBrowserProofApiV1AuthCsrfGet]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
) -> Response[ErrorResponse | GetAuthCsrfResponseBrowserProofApiV1AuthCsrfGet]:
    """Browser Proof

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | GetAuthCsrfResponseBrowserProofApiV1AuthCsrfGet]
    """

    kwargs = build_request()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient,
) -> ErrorResponse | GetAuthCsrfResponseBrowserProofApiV1AuthCsrfGet | None:
    """Browser Proof

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | GetAuthCsrfResponseBrowserProofApiV1AuthCsrfGet
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
) -> Response[ErrorResponse | GetAuthCsrfResponseBrowserProofApiV1AuthCsrfGet]:
    """Browser Proof

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | GetAuthCsrfResponseBrowserProofApiV1AuthCsrfGet]
    """

    kwargs = build_request()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
) -> ErrorResponse | GetAuthCsrfResponseBrowserProofApiV1AuthCsrfGet | None:
    """Browser Proof

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | GetAuthCsrfResponseBrowserProofApiV1AuthCsrfGet
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
