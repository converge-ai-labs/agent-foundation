from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.model_provider_collection import ModelProviderCollection
from ...types import UNSET, Response, Unset


def build_request(
    organization: str,
    *,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    name: str | Unset | None = UNSET,
    provider_type: str | Unset | None = UNSET,
    enabled: bool | Unset | None = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    json_cursor: str | Unset | None
    if isinstance(cursor, Unset):
        json_cursor = UNSET
    else:
        json_cursor = cursor
    params["cursor"] = json_cursor

    json_name: str | Unset | None
    if isinstance(name, Unset):
        json_name = UNSET
    else:
        json_name = name
    params["name"] = json_name

    json_provider_type: str | Unset | None
    if isinstance(provider_type, Unset):
        json_provider_type = UNSET
    else:
        json_provider_type = provider_type
    params["provider_type"] = json_provider_type

    json_enabled: bool | Unset | None
    if isinstance(enabled, Unset):
        json_enabled = UNSET
    else:
        json_enabled = enabled
    params["enabled"] = json_enabled

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/organizations/{organization}/model-providers".format(
            organization=quote(str(organization), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ModelProviderCollection:
    if response.status_code == 200:
        response_200 = ModelProviderCollection.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | ModelProviderCollection]:
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
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    name: str | Unset | None = UNSET,
    provider_type: str | Unset | None = UNSET,
    enabled: bool | Unset | None = UNSET,
) -> Response[ErrorResponse | ModelProviderCollection]:
    """Organization List Model Providers

    Args:
        organization (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        name (None | str | Unset):
        provider_type (None | str | Unset):
        enabled (bool | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ModelProviderCollection]
    """

    kwargs = build_request(
        organization=organization,
        limit=limit,
        cursor=cursor,
        name=name,
        provider_type=provider_type,
        enabled=enabled,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    organization: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    name: str | Unset | None = UNSET,
    provider_type: str | Unset | None = UNSET,
    enabled: bool | Unset | None = UNSET,
) -> ErrorResponse | ModelProviderCollection | None:
    """Organization List Model Providers

    Args:
        organization (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        name (None | str | Unset):
        provider_type (None | str | Unset):
        enabled (bool | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ModelProviderCollection
    """

    return sync_detailed(
        organization=organization,
        client=client,
        limit=limit,
        cursor=cursor,
        name=name,
        provider_type=provider_type,
        enabled=enabled,
    ).parsed


async def asyncio_detailed(
    organization: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    name: str | Unset | None = UNSET,
    provider_type: str | Unset | None = UNSET,
    enabled: bool | Unset | None = UNSET,
) -> Response[ErrorResponse | ModelProviderCollection]:
    """Organization List Model Providers

    Args:
        organization (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        name (None | str | Unset):
        provider_type (None | str | Unset):
        enabled (bool | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ModelProviderCollection]
    """

    kwargs = build_request(
        organization=organization,
        limit=limit,
        cursor=cursor,
        name=name,
        provider_type=provider_type,
        enabled=enabled,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    name: str | Unset | None = UNSET,
    provider_type: str | Unset | None = UNSET,
    enabled: bool | Unset | None = UNSET,
) -> ErrorResponse | ModelProviderCollection | None:
    """Organization List Model Providers

    Args:
        organization (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        name (None | str | Unset):
        provider_type (None | str | Unset):
        enabled (bool | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ModelProviderCollection
    """

    return (
        await asyncio_detailed(
            organization=organization,
            client=client,
            limit=limit,
            cursor=cursor,
            name=name,
            provider_type=provider_type,
            enabled=enabled,
        )
    ).parsed
