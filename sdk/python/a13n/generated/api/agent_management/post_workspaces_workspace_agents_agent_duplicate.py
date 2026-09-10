from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.agent import Agent
from ...models.duplicate_agent_request import DuplicateAgentRequest
from ...models.error_response import ErrorResponse
from ...types import Response


def build_request(
    workspace: str,
    agent: str,
    *,
    body: DuplicateAgentRequest,
    idempotency_key: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/workspaces/{workspace}/agents/{agent}/duplicate".format(
            workspace=quote(str(workspace), safe=""),
            agent=quote(str(agent), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Agent | ErrorResponse:
    if response.status_code == 201:
        response_201 = Agent.from_dict(response.json())

        return response_201

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Agent | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    workspace: str,
    agent: str,
    *,
    client: AuthenticatedClient,
    body: DuplicateAgentRequest,
    idempotency_key: str,
) -> Response[Agent | ErrorResponse]:
    """Duplicate Agent

    Args:
        workspace (str):
        agent (str):
        idempotency_key (str):
        body (DuplicateAgentRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Agent | ErrorResponse]
    """

    kwargs = build_request(
        workspace=workspace,
        agent=agent,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    workspace: str,
    agent: str,
    *,
    client: AuthenticatedClient,
    body: DuplicateAgentRequest,
    idempotency_key: str,
) -> Agent | ErrorResponse | None:
    """Duplicate Agent

    Args:
        workspace (str):
        agent (str):
        idempotency_key (str):
        body (DuplicateAgentRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Agent | ErrorResponse
    """

    return sync_detailed(
        workspace=workspace,
        agent=agent,
        client=client,
        body=body,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    agent: str,
    *,
    client: AuthenticatedClient,
    body: DuplicateAgentRequest,
    idempotency_key: str,
) -> Response[Agent | ErrorResponse]:
    """Duplicate Agent

    Args:
        workspace (str):
        agent (str):
        idempotency_key (str):
        body (DuplicateAgentRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Agent | ErrorResponse]
    """

    kwargs = build_request(
        workspace=workspace,
        agent=agent,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    agent: str,
    *,
    client: AuthenticatedClient,
    body: DuplicateAgentRequest,
    idempotency_key: str,
) -> Agent | ErrorResponse | None:
    """Duplicate Agent

    Args:
        workspace (str):
        agent (str):
        idempotency_key (str):
        body (DuplicateAgentRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Agent | ErrorResponse
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            agent=agent,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed
