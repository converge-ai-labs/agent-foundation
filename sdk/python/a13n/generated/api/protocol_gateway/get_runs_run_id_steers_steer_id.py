from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.steer_status import SteerStatus
from ...types import Response


def build_request(
    run_id: str,
    steer_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/runs/{run_id}/steers/{steer_id}".format(
            run_id=quote(str(run_id), safe=""),
            steer_id=quote(str(steer_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> ErrorResponse | SteerStatus:
    if response.status_code == 200:
        response_200 = SteerStatus.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | SteerStatus]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    run_id: str,
    steer_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[ErrorResponse | SteerStatus]:
    """Get Run Steer

    Args:
        run_id (str):
        steer_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SteerStatus]
    """

    kwargs = build_request(
        run_id=run_id,
        steer_id=steer_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    run_id: str,
    steer_id: str,
    *,
    client: AuthenticatedClient,
) -> ErrorResponse | SteerStatus | None:
    """Get Run Steer

    Args:
        run_id (str):
        steer_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SteerStatus
    """

    return sync_detailed(
        run_id=run_id,
        steer_id=steer_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    run_id: str,
    steer_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[ErrorResponse | SteerStatus]:
    """Get Run Steer

    Args:
        run_id (str):
        steer_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SteerStatus]
    """

    kwargs = build_request(
        run_id=run_id,
        steer_id=steer_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    run_id: str,
    steer_id: str,
    *,
    client: AuthenticatedClient,
) -> ErrorResponse | SteerStatus | None:
    """Get Run Steer

    Args:
        run_id (str):
        steer_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SteerStatus
    """

    return (
        await asyncio_detailed(
            run_id=run_id,
            steer_id=steer_id,
            client=client,
        )
    ).parsed
