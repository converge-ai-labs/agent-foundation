from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.account import Account
from ...models.account_command_request import AccountCommandRequest
from ...models.error_response import ErrorResponse
from ...models.post_application_accounts_account_id_action_action import PostApplicationAccountsAccountIdActionAction
from ...types import Response


def build_request(
    account_id: str,
    action: PostApplicationAccountsAccountIdActionAction,
    *,
    body: AccountCommandRequest,
    idempotency_key: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/application-accounts/{account_id}/{action}".format(
            account_id=quote(str(account_id), safe=""),
            action=quote(str(action), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Account | ErrorResponse:
    if response.status_code == 200:
        response_200 = Account.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Account | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    account_id: str,
    action: PostApplicationAccountsAccountIdActionAction,
    *,
    client: AuthenticatedClient,
    body: AccountCommandRequest,
    idempotency_key: str,
) -> Response[Account | ErrorResponse]:
    """Change Account Lifecycle

    Args:
        account_id (str):
        action (PostApplicationAccountsAccountIdActionAction):
        idempotency_key (str):
        body (AccountCommandRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Account | ErrorResponse]
    """

    kwargs = build_request(
        account_id=account_id,
        action=action,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    account_id: str,
    action: PostApplicationAccountsAccountIdActionAction,
    *,
    client: AuthenticatedClient,
    body: AccountCommandRequest,
    idempotency_key: str,
) -> Account | ErrorResponse | None:
    """Change Account Lifecycle

    Args:
        account_id (str):
        action (PostApplicationAccountsAccountIdActionAction):
        idempotency_key (str):
        body (AccountCommandRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Account | ErrorResponse
    """

    return sync_detailed(
        account_id=account_id,
        action=action,
        client=client,
        body=body,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    account_id: str,
    action: PostApplicationAccountsAccountIdActionAction,
    *,
    client: AuthenticatedClient,
    body: AccountCommandRequest,
    idempotency_key: str,
) -> Response[Account | ErrorResponse]:
    """Change Account Lifecycle

    Args:
        account_id (str):
        action (PostApplicationAccountsAccountIdActionAction):
        idempotency_key (str):
        body (AccountCommandRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Account | ErrorResponse]
    """

    kwargs = build_request(
        account_id=account_id,
        action=action,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    account_id: str,
    action: PostApplicationAccountsAccountIdActionAction,
    *,
    client: AuthenticatedClient,
    body: AccountCommandRequest,
    idempotency_key: str,
) -> Account | ErrorResponse | None:
    """Change Account Lifecycle

    Args:
        account_id (str):
        action (PostApplicationAccountsAccountIdActionAction):
        idempotency_key (str):
        body (AccountCommandRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Account | ErrorResponse
    """

    return (
        await asyncio_detailed(
            account_id=account_id,
            action=action,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed
