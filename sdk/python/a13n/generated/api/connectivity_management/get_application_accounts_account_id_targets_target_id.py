from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.account_target import AccountTarget
from ...models.error_response import ErrorResponse
from ...types import Response


def build_request(
    account_id: str,
    target_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/application-accounts/{account_id}/targets/{target_id}".format(
            account_id=quote(str(account_id), safe=""),
            target_id=quote(str(target_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> AccountTarget | ErrorResponse:
    if response.status_code == 200:
        response_200 = AccountTarget.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[AccountTarget | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    account_id: str,
    target_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[AccountTarget | ErrorResponse]:
    """Get

    Args:
        account_id (str):
        target_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AccountTarget | ErrorResponse]
    """

    kwargs = build_request(
        account_id=account_id,
        target_id=target_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    account_id: str,
    target_id: str,
    *,
    client: AuthenticatedClient,
) -> AccountTarget | ErrorResponse | None:
    """Get

    Args:
        account_id (str):
        target_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AccountTarget | ErrorResponse
    """

    return sync_detailed(
        account_id=account_id,
        target_id=target_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    account_id: str,
    target_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[AccountTarget | ErrorResponse]:
    """Get

    Args:
        account_id (str):
        target_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AccountTarget | ErrorResponse]
    """

    kwargs = build_request(
        account_id=account_id,
        target_id=target_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    account_id: str,
    target_id: str,
    *,
    client: AuthenticatedClient,
) -> AccountTarget | ErrorResponse | None:
    """Get

    Args:
        account_id (str):
        target_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AccountTarget | ErrorResponse
    """

    return (
        await asyncio_detailed(
            account_id=account_id,
            target_id=target_id,
            client=client,
        )
    ).parsed
