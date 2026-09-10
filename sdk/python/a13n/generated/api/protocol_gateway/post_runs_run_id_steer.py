from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.agent_input import AgentInput
from ...models.error_response import ErrorResponse
from ...models.steer_receipt import SteerReceipt
from ...types import Response


def build_request(
    run_id: str,
    *,
    body: AgentInput,
    idempotency_key: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/runs/{run_id}/steer".format(
            run_id=quote(str(run_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> ErrorResponse | SteerReceipt:
    if response.status_code == 202:
        response_202 = SteerReceipt.from_dict(response.json())

        return response_202

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | SteerReceipt]:
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
    body: AgentInput,
    idempotency_key: str,
) -> Response[ErrorResponse | SteerReceipt]:
    """Steer Run

    Args:
        run_id (str):
        idempotency_key (str):
        body (AgentInput): Submitted or retained versioned ordinary Agent input.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SteerReceipt]
    """

    kwargs = build_request(
        run_id=run_id,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    run_id: str,
    *,
    client: AuthenticatedClient,
    body: AgentInput,
    idempotency_key: str,
) -> ErrorResponse | SteerReceipt | None:
    """Steer Run

    Args:
        run_id (str):
        idempotency_key (str):
        body (AgentInput): Submitted or retained versioned ordinary Agent input.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SteerReceipt
    """

    return sync_detailed(
        run_id=run_id,
        client=client,
        body=body,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    run_id: str,
    *,
    client: AuthenticatedClient,
    body: AgentInput,
    idempotency_key: str,
) -> Response[ErrorResponse | SteerReceipt]:
    """Steer Run

    Args:
        run_id (str):
        idempotency_key (str):
        body (AgentInput): Submitted or retained versioned ordinary Agent input.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SteerReceipt]
    """

    kwargs = build_request(
        run_id=run_id,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    run_id: str,
    *,
    client: AuthenticatedClient,
    body: AgentInput,
    idempotency_key: str,
) -> ErrorResponse | SteerReceipt | None:
    """Steer Run

    Args:
        run_id (str):
        idempotency_key (str):
        body (AgentInput): Submitted or retained versioned ordinary Agent input.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SteerReceipt
    """

    return (
        await asyncio_detailed(
            run_id=run_id,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed
