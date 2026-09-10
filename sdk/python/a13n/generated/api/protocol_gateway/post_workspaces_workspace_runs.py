from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.run_acceptance_receipt import RunAcceptanceReceipt
from ...models.start_run_request import StartRunRequest
from ...types import Response


def build_request(
    workspace: str,
    *,
    body: StartRunRequest,
    idempotency_key: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/workspaces/{workspace}/runs".format(
            workspace=quote(str(workspace), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | RunAcceptanceReceipt:
    if response.status_code == 202:
        response_202 = RunAcceptanceReceipt.from_dict(response.json())

        return response_202

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | RunAcceptanceReceipt]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: StartRunRequest,
    idempotency_key: str,
) -> Response[ErrorResponse | RunAcceptanceReceipt]:
    """Start Run

    Args:
        workspace (str):
        idempotency_key (str):
        body (StartRunRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | RunAcceptanceReceipt]
    """

    kwargs = build_request(
        workspace=workspace,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: StartRunRequest,
    idempotency_key: str,
) -> ErrorResponse | RunAcceptanceReceipt | None:
    """Start Run

    Args:
        workspace (str):
        idempotency_key (str):
        body (StartRunRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | RunAcceptanceReceipt
    """

    return sync_detailed(
        workspace=workspace,
        client=client,
        body=body,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: StartRunRequest,
    idempotency_key: str,
) -> Response[ErrorResponse | RunAcceptanceReceipt]:
    """Start Run

    Args:
        workspace (str):
        idempotency_key (str):
        body (StartRunRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | RunAcceptanceReceipt]
    """

    kwargs = build_request(
        workspace=workspace,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: StartRunRequest,
    idempotency_key: str,
) -> ErrorResponse | RunAcceptanceReceipt | None:
    """Start Run

    Args:
        workspace (str):
        idempotency_key (str):
        body (StartRunRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | RunAcceptanceReceipt
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed
