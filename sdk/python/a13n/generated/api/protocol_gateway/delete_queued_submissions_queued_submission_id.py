from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.delete_queued_submission_request import DeleteQueuedSubmissionRequest
from ...models.error_response import ErrorResponse
from ...models.thread_queue_mutation_receipt import ThreadQueueMutationReceipt
from ...types import Response


def build_request(
    queued_submission_id: str,
    *,
    body: DeleteQueuedSubmissionRequest,
    idempotency_key: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "delete",
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
) -> ErrorResponse | ThreadQueueMutationReceipt:
    if response.status_code == 200:
        response_200 = ThreadQueueMutationReceipt.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | ThreadQueueMutationReceipt]:
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
    body: DeleteQueuedSubmissionRequest,
    idempotency_key: str,
) -> Response[ErrorResponse | ThreadQueueMutationReceipt]:
    """Delete Queued Submission

    Args:
        queued_submission_id (str):
        idempotency_key (str):
        body (DeleteQueuedSubmissionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ThreadQueueMutationReceipt]
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
    body: DeleteQueuedSubmissionRequest,
    idempotency_key: str,
) -> ErrorResponse | ThreadQueueMutationReceipt | None:
    """Delete Queued Submission

    Args:
        queued_submission_id (str):
        idempotency_key (str):
        body (DeleteQueuedSubmissionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ThreadQueueMutationReceipt
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
    body: DeleteQueuedSubmissionRequest,
    idempotency_key: str,
) -> Response[ErrorResponse | ThreadQueueMutationReceipt]:
    """Delete Queued Submission

    Args:
        queued_submission_id (str):
        idempotency_key (str):
        body (DeleteQueuedSubmissionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ThreadQueueMutationReceipt]
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
    body: DeleteQueuedSubmissionRequest,
    idempotency_key: str,
) -> ErrorResponse | ThreadQueueMutationReceipt | None:
    """Delete Queued Submission

    Args:
        queued_submission_id (str):
        idempotency_key (str):
        body (DeleteQueuedSubmissionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ThreadQueueMutationReceipt
    """

    return (
        await asyncio_detailed(
            queued_submission_id=queued_submission_id,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed
