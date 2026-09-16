from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.bot_check_history import BotCheckHistory
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def build_request(
    account_id: str,
    *,
    conversation_id: str | Unset | None = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_conversation_id: str | Unset | None
    if isinstance(conversation_id, Unset):
        json_conversation_id = UNSET
    else:
        json_conversation_id = conversation_id
    params["conversation_id"] = json_conversation_id

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/application-accounts/{account_id}/bot/checks/latest".format(
            account_id=quote(str(account_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> BotCheckHistory | ErrorResponse:
    if response.status_code == 200:
        response_200 = BotCheckHistory.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[BotCheckHistory | ErrorResponse]:
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
    conversation_id: str | Unset | None = UNSET,
) -> Response[BotCheckHistory | ErrorResponse]:
    """Latest Bot Check

    Args:
        account_id (str):
        conversation_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[BotCheckHistory | ErrorResponse]
    """

    kwargs = build_request(
        account_id=account_id,
        conversation_id=conversation_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    account_id: str,
    *,
    client: AuthenticatedClient,
    conversation_id: str | Unset | None = UNSET,
) -> BotCheckHistory | ErrorResponse | None:
    """Latest Bot Check

    Args:
        account_id (str):
        conversation_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        BotCheckHistory | ErrorResponse
    """

    return sync_detailed(
        account_id=account_id,
        client=client,
        conversation_id=conversation_id,
    ).parsed


async def asyncio_detailed(
    account_id: str,
    *,
    client: AuthenticatedClient,
    conversation_id: str | Unset | None = UNSET,
) -> Response[BotCheckHistory | ErrorResponse]:
    """Latest Bot Check

    Args:
        account_id (str):
        conversation_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[BotCheckHistory | ErrorResponse]
    """

    kwargs = build_request(
        account_id=account_id,
        conversation_id=conversation_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    account_id: str,
    *,
    client: AuthenticatedClient,
    conversation_id: str | Unset | None = UNSET,
) -> BotCheckHistory | ErrorResponse | None:
    """Latest Bot Check

    Args:
        account_id (str):
        conversation_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        BotCheckHistory | ErrorResponse
    """

    return (
        await asyncio_detailed(
            account_id=account_id,
            client=client,
            conversation_id=conversation_id,
        )
    ).parsed
