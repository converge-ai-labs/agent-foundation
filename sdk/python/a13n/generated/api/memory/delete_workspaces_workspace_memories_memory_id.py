from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.mem_0_scope import Mem0Scope
from ...types import UNSET, Response, Unset


def build_request(
    workspace: str,
    memory_id: str,
    *,
    scope: Mem0Scope,
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
        "method": "delete",
        "url": "/api/v1/workspaces/{workspace}/memories/{memory_id}".format(
            workspace=quote(str(workspace), safe=""),
            memory_id=quote(str(memory_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Any | ErrorResponse:
    if response.status_code == 204:
        response_204 = cast(Any, None)
        return response_204

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Response[Any | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    workspace: str,
    memory_id: str,
    *,
    client: AuthenticatedClient,
    scope: Mem0Scope,
    subject_id: str | Unset | None = UNSET,
) -> Response[Any | ErrorResponse]:
    """Delete Memory

    Args:
        workspace (str):
        memory_id (str):
        scope (Mem0Scope): Trusted Harness identity boundary used for Mem0 records.
        subject_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorResponse]
    """

    kwargs = build_request(
        workspace=workspace,
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
    memory_id: str,
    *,
    client: AuthenticatedClient,
    scope: Mem0Scope,
    subject_id: str | Unset | None = UNSET,
) -> Any | ErrorResponse | None:
    """Delete Memory

    Args:
        workspace (str):
        memory_id (str):
        scope (Mem0Scope): Trusted Harness identity boundary used for Mem0 records.
        subject_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorResponse
    """

    return sync_detailed(
        workspace=workspace,
        memory_id=memory_id,
        client=client,
        scope=scope,
        subject_id=subject_id,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    memory_id: str,
    *,
    client: AuthenticatedClient,
    scope: Mem0Scope,
    subject_id: str | Unset | None = UNSET,
) -> Response[Any | ErrorResponse]:
    """Delete Memory

    Args:
        workspace (str):
        memory_id (str):
        scope (Mem0Scope): Trusted Harness identity boundary used for Mem0 records.
        subject_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorResponse]
    """

    kwargs = build_request(
        workspace=workspace,
        memory_id=memory_id,
        scope=scope,
        subject_id=subject_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    memory_id: str,
    *,
    client: AuthenticatedClient,
    scope: Mem0Scope,
    subject_id: str | Unset | None = UNSET,
) -> Any | ErrorResponse | None:
    """Delete Memory

    Args:
        workspace (str):
        memory_id (str):
        scope (Mem0Scope): Trusted Harness identity boundary used for Mem0 records.
        subject_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorResponse
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            memory_id=memory_id,
            client=client,
            scope=scope,
            subject_id=subject_id,
        )
    ).parsed
