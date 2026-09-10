from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.consume_queued_submission_request import ConsumeQueuedSubmissionRequest
from ...models.error_response import ErrorResponse
from ...models.queued_submission_consumption_receipt import QueuedSubmissionConsumptionReceipt
from ...types import Response


def build_request(
    thread_id: str,
    *,
    body: ConsumeQueuedSubmissionRequest,
    idempotency_key: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/threads/{thread_id}/queued-submissions/consume".format(
            thread_id=quote(str(thread_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | QueuedSubmissionConsumptionReceipt:
    if response.status_code == 202:
        response_202 = QueuedSubmissionConsumptionReceipt.from_dict(response.json())

        return response_202

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | QueuedSubmissionConsumptionReceipt]:
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
    body: ConsumeQueuedSubmissionRequest,
    idempotency_key: str,
) -> Response[ErrorResponse | QueuedSubmissionConsumptionReceipt]:
    """Consume Queued Submission

    Args:
        thread_id (str):
        idempotency_key (str):
        body (ConsumeQueuedSubmissionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | QueuedSubmissionConsumptionReceipt]
    """

    kwargs = build_request(
        thread_id=thread_id,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    thread_id: str,
    *,
    client: AuthenticatedClient,
    body: ConsumeQueuedSubmissionRequest,
    idempotency_key: str,
) -> ErrorResponse | QueuedSubmissionConsumptionReceipt | None:
    """Consume Queued Submission

    Args:
        thread_id (str):
        idempotency_key (str):
        body (ConsumeQueuedSubmissionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | QueuedSubmissionConsumptionReceipt
    """

    return sync_detailed(
        thread_id=thread_id,
        client=client,
        body=body,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    thread_id: str,
    *,
    client: AuthenticatedClient,
    body: ConsumeQueuedSubmissionRequest,
    idempotency_key: str,
) -> Response[ErrorResponse | QueuedSubmissionConsumptionReceipt]:
    """Consume Queued Submission

    Args:
        thread_id (str):
        idempotency_key (str):
        body (ConsumeQueuedSubmissionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | QueuedSubmissionConsumptionReceipt]
    """

    kwargs = build_request(
        thread_id=thread_id,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    thread_id: str,
    *,
    client: AuthenticatedClient,
    body: ConsumeQueuedSubmissionRequest,
    idempotency_key: str,
) -> ErrorResponse | QueuedSubmissionConsumptionReceipt | None:
    """Consume Queued Submission

    Args:
        thread_id (str):
        idempotency_key (str):
        body (ConsumeQueuedSubmissionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | QueuedSubmissionConsumptionReceipt
    """

    return (
        await asyncio_detailed(
            thread_id=thread_id,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed
