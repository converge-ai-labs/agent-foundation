from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.asset_collection import AssetCollection
from ...models.asset_source_kind import AssetSourceKind
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def build_request(
    workspace: str,
    *,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    source_kind: AssetSourceKind | Unset | None = UNSET,
    source_run_id: str | Unset | None = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    json_cursor: str | Unset | None
    if isinstance(cursor, Unset):
        json_cursor = UNSET
    else:
        json_cursor = cursor
    params["cursor"] = json_cursor

    json_source_kind: str | Unset | None
    if isinstance(source_kind, Unset):
        json_source_kind = UNSET
    elif isinstance(source_kind, AssetSourceKind):
        json_source_kind = source_kind.value
    else:
        json_source_kind = source_kind
    params["source_kind"] = json_source_kind

    json_source_run_id: str | Unset | None
    if isinstance(source_run_id, Unset):
        json_source_run_id = UNSET
    else:
        json_source_run_id = source_run_id
    params["source_run_id"] = json_source_run_id

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/workspaces/{workspace}/assets".format(
            workspace=quote(str(workspace), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AssetCollection | ErrorResponse:
    if response.status_code == 200:
        response_200 = AssetCollection.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[AssetCollection | ErrorResponse]:
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
    source_kind: AssetSourceKind | Unset | None = UNSET,
    source_run_id: str | Unset | None = UNSET,
) -> Response[AssetCollection | ErrorResponse]:
    """List Assets

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        source_kind (AssetSourceKind | None | Unset):
        source_run_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AssetCollection | ErrorResponse]
    """

    kwargs = build_request(
        workspace=workspace,
        limit=limit,
        cursor=cursor,
        source_kind=source_kind,
        source_run_id=source_run_id,
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
    source_kind: AssetSourceKind | Unset | None = UNSET,
    source_run_id: str | Unset | None = UNSET,
) -> AssetCollection | ErrorResponse | None:
    """List Assets

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        source_kind (AssetSourceKind | None | Unset):
        source_run_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AssetCollection | ErrorResponse
    """

    return sync_detailed(
        workspace=workspace,
        client=client,
        limit=limit,
        cursor=cursor,
        source_kind=source_kind,
        source_run_id=source_run_id,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    source_kind: AssetSourceKind | Unset | None = UNSET,
    source_run_id: str | Unset | None = UNSET,
) -> Response[AssetCollection | ErrorResponse]:
    """List Assets

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        source_kind (AssetSourceKind | None | Unset):
        source_run_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AssetCollection | ErrorResponse]
    """

    kwargs = build_request(
        workspace=workspace,
        limit=limit,
        cursor=cursor,
        source_kind=source_kind,
        source_run_id=source_run_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    source_kind: AssetSourceKind | Unset | None = UNSET,
    source_run_id: str | Unset | None = UNSET,
) -> AssetCollection | ErrorResponse | None:
    """List Assets

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        source_kind (AssetSourceKind | None | Unset):
        source_run_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AssetCollection | ErrorResponse
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            client=client,
            limit=limit,
            cursor=cursor,
            source_kind=source_kind,
            source_run_id=source_run_id,
        )
    ).parsed
