from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.environment import Environment
from ...models.error_response import ErrorResponse
from ...models.new_environment_selection import NewEnvironmentSelection
from ...models.register_environment_request import RegisterEnvironmentRequest
from ...types import Response


def build_request(
    workspace: str,
    *,
    body: NewEnvironmentSelection | RegisterEnvironmentRequest,
    idempotency_key: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/workspaces/{workspace}/environments".format(
            workspace=quote(str(workspace), safe=""),
        ),
    }

    if isinstance(body, NewEnvironmentSelection):
        _kwargs["json"] = body.to_dict()
    else:
        _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Environment | ErrorResponse:
    if response.status_code == 201:
        response_201 = Environment.from_dict(response.json())

        return response_201

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Environment | ErrorResponse]:
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
    body: NewEnvironmentSelection | RegisterEnvironmentRequest,
    idempotency_key: str,
) -> Response[Environment | ErrorResponse]:
    """Create Environment

    Args:
        workspace (str):
        idempotency_key (str):
        body (NewEnvironmentSelection | RegisterEnvironmentRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Environment | ErrorResponse]
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
    body: NewEnvironmentSelection | RegisterEnvironmentRequest,
    idempotency_key: str,
) -> Environment | ErrorResponse | None:
    """Create Environment

    Args:
        workspace (str):
        idempotency_key (str):
        body (NewEnvironmentSelection | RegisterEnvironmentRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Environment | ErrorResponse
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
    body: NewEnvironmentSelection | RegisterEnvironmentRequest,
    idempotency_key: str,
) -> Response[Environment | ErrorResponse]:
    """Create Environment

    Args:
        workspace (str):
        idempotency_key (str):
        body (NewEnvironmentSelection | RegisterEnvironmentRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Environment | ErrorResponse]
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
    body: NewEnvironmentSelection | RegisterEnvironmentRequest,
    idempotency_key: str,
) -> Environment | ErrorResponse | None:
    """Create Environment

    Args:
        workspace (str):
        idempotency_key (str):
        body (NewEnvironmentSelection | RegisterEnvironmentRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Environment | ErrorResponse
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed
