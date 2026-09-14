from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response


def build_request(
    account_id: str,
    target_id: str,
    *,
    expected_version: int,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["expected_version"] = expected_version

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/api/v1/application-accounts/{account_id}/targets/{target_id}".format(
            account_id=quote(str(account_id), safe=""),
            target_id=quote(str(target_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Any | ErrorResponse:
    if response.status_code == 204:
        response_204 = cast(Any, None)
        return response_204

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Response[Any | ErrorResponse]:
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
    expected_version: int,
) -> Response[Any | ErrorResponse]:
    """Delete

    Args:
        account_id (str):
        target_id (str):
        expected_version (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorResponse]
    """

    kwargs = build_request(
        account_id=account_id,
        target_id=target_id,
        expected_version=expected_version,
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
    expected_version: int,
) -> Any | ErrorResponse | None:
    """Delete

    Args:
        account_id (str):
        target_id (str):
        expected_version (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorResponse
    """

    return sync_detailed(
        account_id=account_id,
        target_id=target_id,
        client=client,
        expected_version=expected_version,
    ).parsed


async def asyncio_detailed(
    account_id: str,
    target_id: str,
    *,
    client: AuthenticatedClient,
    expected_version: int,
) -> Response[Any | ErrorResponse]:
    """Delete

    Args:
        account_id (str):
        target_id (str):
        expected_version (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorResponse]
    """

    kwargs = build_request(
        account_id=account_id,
        target_id=target_id,
        expected_version=expected_version,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    account_id: str,
    target_id: str,
    *,
    client: AuthenticatedClient,
    expected_version: int,
) -> Any | ErrorResponse | None:
    """Delete

    Args:
        account_id (str):
        target_id (str):
        expected_version (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorResponse
    """

    return (
        await asyncio_detailed(
            account_id=account_id,
            target_id=target_id,
            client=client,
            expected_version=expected_version,
        )
    ).parsed
