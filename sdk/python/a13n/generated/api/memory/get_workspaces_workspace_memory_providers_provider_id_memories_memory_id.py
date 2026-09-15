from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.memory import Memory
from ...models.memory_scope import MemoryScope
from ...types import UNSET, Response, Unset


def build_request(
    workspace: str,
    provider_id: str,
    memory_id: str,
    *,
    scope: MemoryScope,
    subject_id: str | Unset | None = UNSET,
) -> dict[str, Any]:

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
        "method": "get",
        "url": "/api/v1/workspaces/{workspace}/memory-providers/{provider_id}/memories/{memory_id}".format(
            workspace=quote(str(workspace), safe=""),
            provider_id=quote(str(provider_id), safe=""),
            memory_id=quote(str(memory_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> ErrorResponse | Memory:
    if response.status_code == 200:
        response_200 = Memory.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | Memory]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    workspace: str,
    provider_id: str,
    memory_id: str,
    *,
    client: AuthenticatedClient,
    scope: MemoryScope,
    subject_id: str | Unset | None = UNSET,
) -> Response[ErrorResponse | Memory]:
    """Get Memory

    Args:
        workspace (str):
        provider_id (str):
        memory_id (str):
        scope (MemoryScope):
        subject_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | Memory]
    """

    kwargs = build_request(
        workspace=workspace,
        provider_id=provider_id,
        memory_id=memory_id,
        scope=scope,
        subject_id=subject_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    workspace: str,
    provider_id: str,
    memory_id: str,
    *,
    client: AuthenticatedClient,
    scope: MemoryScope,
    subject_id: str | Unset | None = UNSET,
) -> ErrorResponse | Memory | None:
    """Get Memory

    Args:
        workspace (str):
        provider_id (str):
        memory_id (str):
        scope (MemoryScope):
        subject_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | Memory
    """

    return sync_detailed(
        workspace=workspace,
        provider_id=provider_id,
        memory_id=memory_id,
        client=client,
        scope=scope,
        subject_id=subject_id,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    provider_id: str,
    memory_id: str,
    *,
    client: AuthenticatedClient,
    scope: MemoryScope,
    subject_id: str | Unset | None = UNSET,
) -> Response[ErrorResponse | Memory]:
    """Get Memory

    Args:
        workspace (str):
        provider_id (str):
        memory_id (str):
        scope (MemoryScope):
        subject_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | Memory]
    """

    kwargs = build_request(
        workspace=workspace,
        provider_id=provider_id,
        memory_id=memory_id,
        scope=scope,
        subject_id=subject_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    provider_id: str,
    memory_id: str,
    *,
    client: AuthenticatedClient,
    scope: MemoryScope,
    subject_id: str | Unset | None = UNSET,
) -> ErrorResponse | Memory | None:
    """Get Memory

    Args:
        workspace (str):
        provider_id (str):
        memory_id (str):
        scope (MemoryScope):
        subject_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | Memory
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            provider_id=provider_id,
            memory_id=memory_id,
            client=client,
            scope=scope,
            subject_id=subject_id,
        )
    ).parsed
