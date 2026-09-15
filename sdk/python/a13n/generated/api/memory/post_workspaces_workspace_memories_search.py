from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.mem_0_scope import Mem0Scope
from ...models.memory_collection import MemoryCollection
from ...models.memory_search import MemorySearch
from ...types import UNSET, Response, Unset


def build_request(
    workspace: str,
    *,
    body: MemorySearch,
    scope: Mem0Scope,
    subject_id: str | Unset | None = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    params: dict[str, Any] = {}

    json_scope = scope.value
    params["scope"] = json_scope

    json_subject_id: str | Unset | None
    if isinstance(subject_id, Unset):
        json_subject_id = UNSET
    else:
        json_subject_id = subject_id
    params["subject_id"] = json_subject_id

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/workspaces/{workspace}/memories/search".format(
            workspace=quote(str(workspace), safe=""),
        ),
        "params": params,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | MemoryCollection:
    if response.status_code == 200:
        response_200 = MemoryCollection.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | MemoryCollection]:
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
    body: MemorySearch,
    scope: Mem0Scope,
    subject_id: str | Unset | None = UNSET,
) -> Response[ErrorResponse | MemoryCollection]:
    """Search Memories

    Args:
        workspace (str):
        scope (Mem0Scope): Trusted Harness identity boundary used for Mem0 records.
        subject_id (None | str | Unset):
        body (MemorySearch):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MemoryCollection]
    """

    kwargs = build_request(
        workspace=workspace,
        body=body,
        scope=scope,
        subject_id=subject_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: MemorySearch,
    scope: Mem0Scope,
    subject_id: str | Unset | None = UNSET,
) -> ErrorResponse | MemoryCollection | None:
    """Search Memories

    Args:
        workspace (str):
        scope (Mem0Scope): Trusted Harness identity boundary used for Mem0 records.
        subject_id (None | str | Unset):
        body (MemorySearch):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MemoryCollection
    """

    return sync_detailed(
        workspace=workspace,
        client=client,
        body=body,
        scope=scope,
        subject_id=subject_id,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: MemorySearch,
    scope: Mem0Scope,
    subject_id: str | Unset | None = UNSET,
) -> Response[ErrorResponse | MemoryCollection]:
    """Search Memories

    Args:
        workspace (str):
        scope (Mem0Scope): Trusted Harness identity boundary used for Mem0 records.
        subject_id (None | str | Unset):
        body (MemorySearch):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MemoryCollection]
    """

    kwargs = build_request(
        workspace=workspace,
        body=body,
        scope=scope,
        subject_id=subject_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: MemorySearch,
    scope: Mem0Scope,
    subject_id: str | Unset | None = UNSET,
) -> ErrorResponse | MemoryCollection | None:
    """Search Memories

    Args:
        workspace (str):
        scope (Mem0Scope): Trusted Harness identity boundary used for Mem0 records.
        subject_id (None | str | Unset):
        body (MemorySearch):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MemoryCollection
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            client=client,
            body=body,
            scope=scope,
            subject_id=subject_id,
        )
    ).parsed
