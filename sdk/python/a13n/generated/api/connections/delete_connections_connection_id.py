from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.connection_cleanup_receipt import ConnectionCleanupReceipt
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response


def build_request(
    connection_id: str,
    *,
    expected_version: int,
    idempotency_key: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["Idempotency-Key"] = idempotency_key

    params: dict[str, Any] = {}

    params["expected_version"] = expected_version

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/api/v1/connections/{connection_id}".format(
            connection_id=quote(str(connection_id), safe=""),
        ),
        "params": params,
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ConnectionCleanupReceipt | ErrorResponse:
    if response.status_code == 200:
        response_200 = ConnectionCleanupReceipt.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ConnectionCleanupReceipt | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    connection_id: str,
    *,
    client: AuthenticatedClient,
    expected_version: int,
    idempotency_key: str,
) -> Response[ConnectionCleanupReceipt | ErrorResponse]:
    """Delete Connection

    Args:
        connection_id (str):
        expected_version (int):
        idempotency_key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConnectionCleanupReceipt | ErrorResponse]
    """

    kwargs = build_request(
        connection_id=connection_id,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    connection_id: str,
    *,
    client: AuthenticatedClient,
    expected_version: int,
    idempotency_key: str,
) -> ConnectionCleanupReceipt | ErrorResponse | None:
    """Delete Connection

    Args:
        connection_id (str):
        expected_version (int):
        idempotency_key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConnectionCleanupReceipt | ErrorResponse
    """

    return sync_detailed(
        connection_id=connection_id,
        client=client,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    connection_id: str,
    *,
    client: AuthenticatedClient,
    expected_version: int,
    idempotency_key: str,
) -> Response[ConnectionCleanupReceipt | ErrorResponse]:
    """Delete Connection

    Args:
        connection_id (str):
        expected_version (int):
        idempotency_key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConnectionCleanupReceipt | ErrorResponse]
    """

    kwargs = build_request(
        connection_id=connection_id,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    connection_id: str,
    *,
    client: AuthenticatedClient,
    expected_version: int,
    idempotency_key: str,
) -> ConnectionCleanupReceipt | ErrorResponse | None:
    """Delete Connection

    Args:
        connection_id (str):
        expected_version (int):
        idempotency_key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConnectionCleanupReceipt | ErrorResponse
    """

    return (
        await asyncio_detailed(
            connection_id=connection_id,
            client=client,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
        )
    ).parsed
