from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...._binary import file_chunks
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.workspace import Workspace
from ...types import File, Response


def build_request(
    workspace: str,
    *,
    body: File,
    if_match: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["If-Match"] = if_match

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/api/v1/workspaces/{workspace}/icon".format(
            workspace=quote(str(workspace), safe=""),
        ),
    }

    _kwargs["content"] = body.payload
    headers["Content-Type"] = "application/octet-stream"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> ErrorResponse | Workspace:
    if response.status_code == 200:
        response_200 = Workspace.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | Workspace]:
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
    body: File,
    if_match: str,
) -> Response[ErrorResponse | Workspace]:
    """Put Workspace Icon

    Args:
        workspace (str):
        if_match (str):
        body (File):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | Workspace]
    """

    kwargs = build_request(
        workspace=workspace,
        body=body,
        if_match=if_match,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: File,
    if_match: str,
) -> ErrorResponse | Workspace | None:
    """Put Workspace Icon

    Args:
        workspace (str):
        if_match (str):
        body (File):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | Workspace
    """

    return sync_detailed(
        workspace=workspace,
        client=client,
        body=body,
        if_match=if_match,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: File,
    if_match: str,
) -> Response[ErrorResponse | Workspace]:
    """Put Workspace Icon

    Args:
        workspace (str):
        if_match (str):
        body (File):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | Workspace]
    """

    kwargs = build_request(
        workspace=workspace,
        body=body,
        if_match=if_match,
    )

    kwargs["content"] = file_chunks(body.payload)

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: File,
    if_match: str,
) -> ErrorResponse | Workspace | None:
    """Put Workspace Icon

    Args:
        workspace (str):
        if_match (str):
        body (File):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | Workspace
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            client=client,
            body=body,
            if_match=if_match,
        )
    ).parsed
