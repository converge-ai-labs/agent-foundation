from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.account import Account
from ...models.create_account_request import CreateAccountRequest
from ...models.error_response import ErrorResponse
from ...types import Response


def build_request(
    workspace: str,
    *,
    body: CreateAccountRequest,
    idempotency_key: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/workspaces/{workspace}/application-accounts".format(
            workspace=quote(str(workspace), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Account | ErrorResponse:
    if response.status_code == 201:
        response_201 = Account.from_dict(response.json())

        return response_201

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
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: CreateAccountRequest,
    idempotency_key: str,
) -> Response[Account | ErrorResponse]:
    """Create Account

    Args:
        workspace (str):
        idempotency_key (str):
        body (CreateAccountRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Account | ErrorResponse]
    """

    kwargs = build_request(
        workspace=workspace,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: CreateAccountRequest,
    idempotency_key: str,
) -> Account | ErrorResponse | None:
    """Create Account

    Args:
        workspace (str):
        idempotency_key (str):
        body (CreateAccountRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Account | ErrorResponse
    """

    return sync_detailed(
        workspace=workspace,
        client=client,
        body=body,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: CreateAccountRequest,
    idempotency_key: str,
) -> Response[Account | ErrorResponse]:
    """Create Account

    Args:
        workspace (str):
        idempotency_key (str):
        body (CreateAccountRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Account | ErrorResponse]
    """

    kwargs = build_request(
        workspace=workspace,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: CreateAccountRequest,
    idempotency_key: str,
) -> Account | ErrorResponse | None:
    """Create Account

    Args:
        workspace (str):
        idempotency_key (str):
        body (CreateAccountRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Account | ErrorResponse
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed
