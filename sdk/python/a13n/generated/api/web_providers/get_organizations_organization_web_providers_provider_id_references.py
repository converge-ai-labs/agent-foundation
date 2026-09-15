from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.web_provider_reference_collection import WebProviderReferenceCollection
from ...types import UNSET, Response, Unset


def build_request(
    organization: str,
    provider_id: str,
    *,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    json_cursor: str | Unset | None
    if isinstance(cursor, Unset):
        json_cursor = UNSET
    else:
        json_cursor = cursor
    params["cursor"] = json_cursor

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/organizations/{organization}/web-providers/{provider_id}/references".format(
            organization=quote(str(organization), safe=""),
            provider_id=quote(str(provider_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | WebProviderReferenceCollection:
    if response.status_code == 200:
        response_200 = WebProviderReferenceCollection.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | WebProviderReferenceCollection]:
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
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
) -> Response[ErrorResponse | WebProviderReferenceCollection]:
    """References Organization Provider

    Args:
        organization (str):
        provider_id (str):
        limit (int | Unset):
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WebProviderReferenceCollection]
    """

    kwargs = build_request(
        organization=organization,
        provider_id=provider_id,
        limit=limit,
        cursor=cursor,
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
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
) -> ErrorResponse | WebProviderReferenceCollection | None:
    """References Organization Provider

    Args:
        organization (str):
        provider_id (str):
        limit (int | Unset):
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WebProviderReferenceCollection
    """

    return sync_detailed(
        organization=organization,
        provider_id=provider_id,
        client=client,
        limit=limit,
        cursor=cursor,
    ).parsed


async def asyncio_detailed(
    organization: str,
    provider_id: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
) -> Response[ErrorResponse | WebProviderReferenceCollection]:
    """References Organization Provider

    Args:
        organization (str):
        provider_id (str):
        limit (int | Unset):
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WebProviderReferenceCollection]
    """

    kwargs = build_request(
        organization=organization,
        provider_id=provider_id,
        limit=limit,
        cursor=cursor,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization: str,
    provider_id: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
) -> ErrorResponse | WebProviderReferenceCollection | None:
    """References Organization Provider

    Args:
        organization (str):
        provider_id (str):
        limit (int | Unset):
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WebProviderReferenceCollection
    """

    return (
        await asyncio_detailed(
            organization=organization,
            provider_id=provider_id,
            client=client,
            limit=limit,
            cursor=cursor,
        )
    ).parsed
