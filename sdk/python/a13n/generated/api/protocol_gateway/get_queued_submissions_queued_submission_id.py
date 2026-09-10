from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.queued_submission import QueuedSubmission
from ...types import Response


def build_request(
    queued_submission_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/queued-submissions/{queued_submission_id}".format(
            queued_submission_id=quote(str(queued_submission_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | QueuedSubmission:
    if response.status_code == 200:
        response_200 = QueuedSubmission.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | QueuedSubmission]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    queued_submission_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[ErrorResponse | QueuedSubmission]:
    """Get Queued Submission

    Args:
        queued_submission_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | QueuedSubmission]
    """

    kwargs = build_request(
        queued_submission_id=queued_submission_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    queued_submission_id: str,
    *,
    client: AuthenticatedClient,
) -> ErrorResponse | QueuedSubmission | None:
    """Get Queued Submission

    Args:
        queued_submission_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | QueuedSubmission
    """

    return sync_detailed(
        queued_submission_id=queued_submission_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    queued_submission_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[ErrorResponse | QueuedSubmission]:
    """Get Queued Submission

    Args:
        queued_submission_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | QueuedSubmission]
    """

    kwargs = build_request(
        queued_submission_id=queued_submission_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    queued_submission_id: str,
    *,
    client: AuthenticatedClient,
) -> ErrorResponse | QueuedSubmission | None:
    """Get Queued Submission

    Args:
        queued_submission_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | QueuedSubmission
    """

    return (
        await asyncio_detailed(
            queued_submission_id=queued_submission_id,
            client=client,
        )
    ).parsed
