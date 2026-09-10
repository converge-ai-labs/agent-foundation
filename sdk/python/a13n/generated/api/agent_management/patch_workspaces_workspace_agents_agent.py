from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.agent import Agent
from ...models.error_response import ErrorResponse
from ...models.update_agent_request import UpdateAgentRequest
from ...types import Response


def build_request(
    workspace: str,
    agent: str,
    *,
    body: UpdateAgentRequest,
    if_match: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["If-Match"] = if_match

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/api/v1/workspaces/{workspace}/agents/{agent}".format(
            workspace=quote(str(workspace), safe=""),
            agent=quote(str(agent), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Agent | ErrorResponse:
    if response.status_code == 200:
        response_200 = Agent.from_dict(response.json())

        return response_200

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
    body: UpdateAgentRequest,
    if_match: str,
) -> Response[Agent | ErrorResponse]:
    """Update Agent

    Args:
        workspace (str):
        agent (str):
        if_match (str):
        body (UpdateAgentRequest):

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
        if_match=if_match,
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
    body: UpdateAgentRequest,
    if_match: str,
) -> Agent | ErrorResponse | None:
    """Update Agent

    Args:
        workspace (str):
        agent (str):
        if_match (str):
        body (UpdateAgentRequest):

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
        if_match=if_match,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    agent: str,
    *,
    client: AuthenticatedClient,
    body: UpdateAgentRequest,
    if_match: str,
) -> Response[Agent | ErrorResponse]:
    """Update Agent

    Args:
        workspace (str):
        agent (str):
        if_match (str):
        body (UpdateAgentRequest):

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
        if_match=if_match,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    agent: str,
    *,
    client: AuthenticatedClient,
    body: UpdateAgentRequest,
    if_match: str,
) -> Agent | ErrorResponse | None:
    """Update Agent

    Args:
        workspace (str):
        agent (str):
        if_match (str):
        body (UpdateAgentRequest):

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
            if_match=if_match,
        )
    ).parsed
