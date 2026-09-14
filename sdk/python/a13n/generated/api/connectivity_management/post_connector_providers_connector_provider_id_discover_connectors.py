from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.connector_collection import ConnectorCollection
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def build_request(
    connector_provider_id: str,
    *,
    query: str | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    limit: int | Unset = UNSET,
    refresh: bool | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["query"] = query

    json_cursor: str | Unset | None
    if isinstance(cursor, Unset):
        json_cursor = UNSET
    else:
        json_cursor = cursor
    params["cursor"] = json_cursor

    params["limit"] = limit

    params["refresh"] = refresh

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/connector-providers/{connector_provider_id}/discover-connectors".format(
            connector_provider_id=quote(str(connector_provider_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ConnectorCollection | ErrorResponse:
    if response.status_code == 200:
        response_200 = ConnectorCollection.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ConnectorCollection | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    connector_provider_id: str,
    *,
    client: AuthenticatedClient,
    query: str | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    limit: int | Unset = UNSET,
    refresh: bool | Unset = UNSET,
) -> Response[ConnectorCollection | ErrorResponse]:
    """Discover Connectors

    Args:
        connector_provider_id (str):
        query (str | Unset):
        cursor (None | str | Unset):
        limit (int | Unset):
        refresh (bool | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConnectorCollection | ErrorResponse]
    """

    kwargs = build_request(
        connector_provider_id=connector_provider_id,
        query=query,
        cursor=cursor,
        limit=limit,
        refresh=refresh,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    connector_provider_id: str,
    *,
    client: AuthenticatedClient,
    query: str | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    limit: int | Unset = UNSET,
    refresh: bool | Unset = UNSET,
) -> ConnectorCollection | ErrorResponse | None:
    """Discover Connectors

    Args:
        connector_provider_id (str):
        query (str | Unset):
        cursor (None | str | Unset):
        limit (int | Unset):
        refresh (bool | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConnectorCollection | ErrorResponse
    """

    return sync_detailed(
        connector_provider_id=connector_provider_id,
        client=client,
        query=query,
        cursor=cursor,
        limit=limit,
        refresh=refresh,
    ).parsed


async def asyncio_detailed(
    connector_provider_id: str,
    *,
    client: AuthenticatedClient,
    query: str | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    limit: int | Unset = UNSET,
    refresh: bool | Unset = UNSET,
) -> Response[ConnectorCollection | ErrorResponse]:
    """Discover Connectors

    Args:
        connector_provider_id (str):
        query (str | Unset):
        cursor (None | str | Unset):
        limit (int | Unset):
        refresh (bool | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConnectorCollection | ErrorResponse]
    """

    kwargs = build_request(
        connector_provider_id=connector_provider_id,
        query=query,
        cursor=cursor,
        limit=limit,
        refresh=refresh,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    connector_provider_id: str,
    *,
    client: AuthenticatedClient,
    query: str | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    limit: int | Unset = UNSET,
    refresh: bool | Unset = UNSET,
) -> ConnectorCollection | ErrorResponse | None:
    """Discover Connectors

    Args:
        connector_provider_id (str):
        query (str | Unset):
        cursor (None | str | Unset):
        limit (int | Unset):
        refresh (bool | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConnectorCollection | ErrorResponse
    """

    return (
        await asyncio_detailed(
            connector_provider_id=connector_provider_id,
            client=client,
            query=query,
            cursor=cursor,
            limit=limit,
            refresh=refresh,
        )
    ).parsed
