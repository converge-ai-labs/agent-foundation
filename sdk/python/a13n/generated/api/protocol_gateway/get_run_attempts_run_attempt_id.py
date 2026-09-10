from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.run_attempt_resource import RunAttemptResource
from ...types import Response


def build_request(
    run_attempt_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/run-attempts/{run_attempt_id}".format(
            run_attempt_id=quote(str(run_attempt_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | RunAttemptResource:
    if response.status_code == 200:
        response_200 = RunAttemptResource.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | RunAttemptResource]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    run_attempt_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[ErrorResponse | RunAttemptResource]:
    """Get Run Attempt

    Args:
        run_attempt_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | RunAttemptResource]
    """

    kwargs = build_request(
        run_attempt_id=run_attempt_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    run_attempt_id: str,
    *,
    client: AuthenticatedClient,
) -> ErrorResponse | RunAttemptResource | None:
    """Get Run Attempt

    Args:
        run_attempt_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | RunAttemptResource
    """

    return sync_detailed(
        run_attempt_id=run_attempt_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    run_attempt_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[ErrorResponse | RunAttemptResource]:
    """Get Run Attempt

    Args:
        run_attempt_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | RunAttemptResource]
    """

    kwargs = build_request(
        run_attempt_id=run_attempt_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    run_attempt_id: str,
    *,
    client: AuthenticatedClient,
) -> ErrorResponse | RunAttemptResource | None:
    """Get Run Attempt

    Args:
        run_attempt_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | RunAttemptResource
    """

    return (
        await asyncio_detailed(
            run_attempt_id=run_attempt_id,
            client=client,
        )
    ).parsed
