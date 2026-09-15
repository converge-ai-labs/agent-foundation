from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.toolset_candidate import ToolsetCandidate
from ...models.toolset_candidate_result import ToolsetCandidateResult
from ...types import Response


def build_request(
    workspace: str,
    *,
    body: ToolsetCandidate,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/workspaces/{workspace}/toolsets/validate".format(
            workspace=quote(str(workspace), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ToolsetCandidateResult:
    if response.status_code == 200:
        response_200 = ToolsetCandidateResult.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | ToolsetCandidateResult]:
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
    body: ToolsetCandidate,
) -> Response[ErrorResponse | ToolsetCandidateResult]:
    """Validate Toolsets

    Args:
        workspace (str):
        body (ToolsetCandidate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ToolsetCandidateResult]
    """

    kwargs = build_request(
        workspace=workspace,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: ToolsetCandidate,
) -> ErrorResponse | ToolsetCandidateResult | None:
    """Validate Toolsets

    Args:
        workspace (str):
        body (ToolsetCandidate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ToolsetCandidateResult
    """

    return sync_detailed(
        workspace=workspace,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: ToolsetCandidate,
) -> Response[ErrorResponse | ToolsetCandidateResult]:
    """Validate Toolsets

    Args:
        workspace (str):
        body (ToolsetCandidate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ToolsetCandidateResult]
    """

    kwargs = build_request(
        workspace=workspace,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: ToolsetCandidate,
) -> ErrorResponse | ToolsetCandidateResult | None:
    """Validate Toolsets

    Args:
        workspace (str):
        body (ToolsetCandidate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ToolsetCandidateResult
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            client=client,
            body=body,
        )
    ).parsed
