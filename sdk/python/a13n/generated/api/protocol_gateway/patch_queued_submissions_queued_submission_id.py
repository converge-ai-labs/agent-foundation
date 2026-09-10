from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.queued_submission_mutation_receipt import QueuedSubmissionMutationReceipt
from ...models.update_queued_submission_request import UpdateQueuedSubmissionRequest
from ...types import Response


def build_request(
    queued_submission_id: str,
    *,
    body: UpdateQueuedSubmissionRequest,
    idempotency_key: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/api/v1/queued-submissions/{queued_submission_id}".format(
            queued_submission_id=quote(str(queued_submission_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | QueuedSubmissionMutationReceipt:
    if response.status_code == 200:
        response_200 = QueuedSubmissionMutationReceipt.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | QueuedSubmissionMutationReceipt]:
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
    body: UpdateQueuedSubmissionRequest,
    idempotency_key: str,
) -> Response[ErrorResponse | QueuedSubmissionMutationReceipt]:
    """Update Queued Submission

    Args:
        queued_submission_id (str):
        idempotency_key (str):
        body (UpdateQueuedSubmissionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | QueuedSubmissionMutationReceipt]
    """

    kwargs = build_request(
        queued_submission_id=queued_submission_id,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    queued_submission_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateQueuedSubmissionRequest,
    idempotency_key: str,
) -> ErrorResponse | QueuedSubmissionMutationReceipt | None:
    """Update Queued Submission

    Args:
        queued_submission_id (str):
        idempotency_key (str):
        body (UpdateQueuedSubmissionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | QueuedSubmissionMutationReceipt
    """

    return sync_detailed(
        queued_submission_id=queued_submission_id,
        client=client,
        body=body,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    queued_submission_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateQueuedSubmissionRequest,
    idempotency_key: str,
) -> Response[ErrorResponse | QueuedSubmissionMutationReceipt]:
    """Update Queued Submission

    Args:
        queued_submission_id (str):
        idempotency_key (str):
        body (UpdateQueuedSubmissionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | QueuedSubmissionMutationReceipt]
    """

    kwargs = build_request(
        queued_submission_id=queued_submission_id,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    queued_submission_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateQueuedSubmissionRequest,
    idempotency_key: str,
) -> ErrorResponse | QueuedSubmissionMutationReceipt | None:
    """Update Queued Submission

    Args:
        queued_submission_id (str):
        idempotency_key (str):
        body (UpdateQueuedSubmissionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | QueuedSubmissionMutationReceipt
    """

    return (
        await asyncio_detailed(
            queued_submission_id=queued_submission_id,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed
