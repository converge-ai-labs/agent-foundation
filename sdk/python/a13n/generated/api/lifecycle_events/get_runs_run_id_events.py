from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.resource_lifecycle_event_page import ResourceLifecycleEventPage
from ...types import UNSET, Response, Unset


def build_request(
    run_id: str,
    *,
    after_resource_seq: int | Unset = UNSET,
    limit: int | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["after_resource_seq"] = after_resource_seq

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/runs/{run_id}/events".format(
            run_id=quote(str(run_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ResourceLifecycleEventPage:
    if response.status_code == 200:
        response_200 = ResourceLifecycleEventPage.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | ResourceLifecycleEventPage]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    run_id: str,
    *,
    client: AuthenticatedClient,
    after_resource_seq: int | Unset = UNSET,
    limit: int | Unset = UNSET,
) -> Response[ErrorResponse | ResourceLifecycleEventPage]:
    """List Run Events

    Args:
        run_id (str):
        after_resource_seq (int | Unset):
        limit (int | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ResourceLifecycleEventPage]
    """

    kwargs = build_request(
        run_id=run_id,
        after_resource_seq=after_resource_seq,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    run_id: str,
    *,
    client: AuthenticatedClient,
    after_resource_seq: int | Unset = UNSET,
    limit: int | Unset = UNSET,
) -> ErrorResponse | ResourceLifecycleEventPage | None:
    """List Run Events

    Args:
        run_id (str):
        after_resource_seq (int | Unset):
        limit (int | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ResourceLifecycleEventPage
    """

    return sync_detailed(
        run_id=run_id,
        client=client,
        after_resource_seq=after_resource_seq,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    run_id: str,
    *,
    client: AuthenticatedClient,
    after_resource_seq: int | Unset = UNSET,
    limit: int | Unset = UNSET,
) -> Response[ErrorResponse | ResourceLifecycleEventPage]:
    """List Run Events

    Args:
        run_id (str):
        after_resource_seq (int | Unset):
        limit (int | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ResourceLifecycleEventPage]
    """

    kwargs = build_request(
        run_id=run_id,
        after_resource_seq=after_resource_seq,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    run_id: str,
    *,
    client: AuthenticatedClient,
    after_resource_seq: int | Unset = UNSET,
    limit: int | Unset = UNSET,
) -> ErrorResponse | ResourceLifecycleEventPage | None:
    """List Run Events

    Args:
        run_id (str):
        after_resource_seq (int | Unset):
        limit (int | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ResourceLifecycleEventPage
    """

    return (
        await asyncio_detailed(
            run_id=run_id,
            client=client,
            after_resource_seq=after_resource_seq,
            limit=limit,
        )
    ).parsed
