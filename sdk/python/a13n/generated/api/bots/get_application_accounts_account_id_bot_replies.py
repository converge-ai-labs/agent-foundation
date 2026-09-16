from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.bot_reply_collection import BotReplyCollection
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def build_request(
    account_id: str,
    *,
    run_id: str,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["run_id"] = run_id

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
        "url": "/api/v1/application-accounts/{account_id}/bot/replies".format(
            account_id=quote(str(account_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> BotReplyCollection | ErrorResponse:
    if response.status_code == 200:
        response_200 = BotReplyCollection.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[BotReplyCollection | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    account_id: str,
    *,
    client: AuthenticatedClient,
    run_id: str,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
) -> Response[BotReplyCollection | ErrorResponse]:
    """List Bot Reply Observations

    Args:
        account_id (str):
        run_id (str):
        limit (int | Unset):
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[BotReplyCollection | ErrorResponse]
    """

    kwargs = build_request(
        account_id=account_id,
        run_id=run_id,
        limit=limit,
        cursor=cursor,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    account_id: str,
    *,
    client: AuthenticatedClient,
    run_id: str,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
) -> BotReplyCollection | ErrorResponse | None:
    """List Bot Reply Observations

    Args:
        account_id (str):
        run_id (str):
        limit (int | Unset):
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        BotReplyCollection | ErrorResponse
    """

    return sync_detailed(
        account_id=account_id,
        client=client,
        run_id=run_id,
        limit=limit,
        cursor=cursor,
    ).parsed


async def asyncio_detailed(
    account_id: str,
    *,
    client: AuthenticatedClient,
    run_id: str,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
) -> Response[BotReplyCollection | ErrorResponse]:
    """List Bot Reply Observations

    Args:
        account_id (str):
        run_id (str):
        limit (int | Unset):
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[BotReplyCollection | ErrorResponse]
    """

    kwargs = build_request(
        account_id=account_id,
        run_id=run_id,
        limit=limit,
        cursor=cursor,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    account_id: str,
    *,
    client: AuthenticatedClient,
    run_id: str,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
) -> BotReplyCollection | ErrorResponse | None:
    """List Bot Reply Observations

    Args:
        account_id (str):
        run_id (str):
        limit (int | Unset):
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        BotReplyCollection | ErrorResponse
    """

    return (
        await asyncio_detailed(
            account_id=account_id,
            client=client,
            run_id=run_id,
            limit=limit,
            cursor=cursor,
        )
    ).parsed
