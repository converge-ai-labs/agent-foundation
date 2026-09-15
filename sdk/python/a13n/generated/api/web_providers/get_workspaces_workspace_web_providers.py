from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.web_provider_collection import WebProviderCollection
from ...types import UNSET, Response, Unset


def build_request(
    workspace: str,
    *,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    type_: str | Unset | None = UNSET,
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

    json_type_: str | Unset | None
    if isinstance(type_, Unset):
        json_type_ = UNSET
    else:
        json_type_ = type_
    params["type"] = json_type_

    json_enabled: bool | Unset | None
    if isinstance(enabled, Unset):
        json_enabled = UNSET
    else:
        json_enabled = enabled
    params["enabled"] = json_enabled

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/workspaces/{workspace}/web-providers".format(
            workspace=quote(str(workspace), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | WebProviderCollection:
    if response.status_code == 200:
        response_200 = WebProviderCollection.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | WebProviderCollection]:
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
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    type_: str | Unset | None = UNSET,
    enabled: bool | Unset | None = UNSET,
) -> Response[ErrorResponse | WebProviderCollection]:
    """List Workspace Provider

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        type_ (None | str | Unset):
        enabled (bool | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WebProviderCollection]
    """

    kwargs = build_request(
        workspace=workspace,
        limit=limit,
        cursor=cursor,
        type_=type_,
        enabled=enabled,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    workspace: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    type_: str | Unset | None = UNSET,
    enabled: bool | Unset | None = UNSET,
) -> ErrorResponse | WebProviderCollection | None:
    """List Workspace Provider

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        type_ (None | str | Unset):
        enabled (bool | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WebProviderCollection
    """

    return sync_detailed(
        workspace=workspace,
        client=client,
        limit=limit,
        cursor=cursor,
        type_=type_,
        enabled=enabled,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    type_: str | Unset | None = UNSET,
    enabled: bool | Unset | None = UNSET,
) -> Response[ErrorResponse | WebProviderCollection]:
    """List Workspace Provider

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        type_ (None | str | Unset):
        enabled (bool | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WebProviderCollection]
    """

    kwargs = build_request(
        workspace=workspace,
        limit=limit,
        cursor=cursor,
        type_=type_,
        enabled=enabled,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    type_: str | Unset | None = UNSET,
    enabled: bool | Unset | None = UNSET,
) -> ErrorResponse | WebProviderCollection | None:
    """List Workspace Provider

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        type_ (None | str | Unset):
        enabled (bool | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WebProviderCollection
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            client=client,
            limit=limit,
            cursor=cursor,
            type_=type_,
            enabled=enabled,
        )
    ).parsed
