from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.queued_submission_collection import QueuedSubmissionCollection
from ...models.queued_submission_state import QueuedSubmissionState
from ...types import UNSET, Response, Unset


def build_request(
    thread_id: str,
    *,
    state: QueuedSubmissionState | Unset = UNSET,
    limit: int | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_state: str | Unset = UNSET
    if not isinstance(state, Unset):
        json_state = state.value

    params["state"] = json_state

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/threads/{thread_id}/queued-submissions".format(
            thread_id=quote(str(thread_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | QueuedSubmissionCollection:
    if response.status_code == 200:
        response_200 = QueuedSubmissionCollection.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | QueuedSubmissionCollection]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    thread_id: str,
    *,
    client: AuthenticatedClient,
    state: QueuedSubmissionState | Unset = UNSET,
    limit: int | Unset = UNSET,
) -> Response[ErrorResponse | QueuedSubmissionCollection]:
    """List Queued Submissions

    Args:
        thread_id (str):
        state (QueuedSubmissionState | Unset):
        limit (int | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | QueuedSubmissionCollection]
    """

    kwargs = build_request(
        thread_id=thread_id,
        state=state,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    thread_id: str,
    *,
    client: AuthenticatedClient,
    state: QueuedSubmissionState | Unset = UNSET,
    limit: int | Unset = UNSET,
) -> ErrorResponse | QueuedSubmissionCollection | None:
    """List Queued Submissions

    Args:
        thread_id (str):
        state (QueuedSubmissionState | Unset):
        limit (int | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | QueuedSubmissionCollection
    """

    return sync_detailed(
        thread_id=thread_id,
        client=client,
        state=state,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    thread_id: str,
    *,
    client: AuthenticatedClient,
    state: QueuedSubmissionState | Unset = UNSET,
    limit: int | Unset = UNSET,
) -> Response[ErrorResponse | QueuedSubmissionCollection]:
    """List Queued Submissions

    Args:
        thread_id (str):
        state (QueuedSubmissionState | Unset):
        limit (int | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | QueuedSubmissionCollection]
    """

    kwargs = build_request(
        thread_id=thread_id,
        state=state,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    thread_id: str,
    *,
    client: AuthenticatedClient,
    state: QueuedSubmissionState | Unset = UNSET,
    limit: int | Unset = UNSET,
) -> ErrorResponse | QueuedSubmissionCollection | None:
    """List Queued Submissions

    Args:
        thread_id (str):
        state (QueuedSubmissionState | Unset):
        limit (int | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | QueuedSubmissionCollection
    """

    return (
        await asyncio_detailed(
            thread_id=thread_id,
            client=client,
            state=state,
            limit=limit,
        )
    ).parsed
