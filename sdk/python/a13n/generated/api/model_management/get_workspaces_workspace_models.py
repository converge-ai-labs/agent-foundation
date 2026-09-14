from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.get_workspaces_workspace_models_scope_type_0 import GetWorkspacesWorkspaceModelsScopeType0
from ...models.model_collection import ModelCollection
from ...types import UNSET, Response, Unset


def build_request(
    workspace: str,
    *,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    query: str | Unset | None = UNSET,
    provider_id: str | Unset | None = UNSET,
    enabled: bool | Unset | None = UNSET,
    scope: GetWorkspacesWorkspaceModelsScopeType0 | Unset | None = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    json_cursor: str | Unset | None
    if isinstance(cursor, Unset):
        json_cursor = UNSET
    else:
        json_cursor = cursor
    params["cursor"] = json_cursor

    json_query: str | Unset | None
    if isinstance(query, Unset):
        json_query = UNSET
    else:
        json_query = query
    params["query"] = json_query

    json_provider_id: str | Unset | None
    if isinstance(provider_id, Unset):
        json_provider_id = UNSET
    else:
        json_provider_id = provider_id
    params["provider_id"] = json_provider_id

    json_enabled: bool | Unset | None
    if isinstance(enabled, Unset):
        json_enabled = UNSET
    else:
        json_enabled = enabled
    params["enabled"] = json_enabled

    json_scope: str | Unset | None
    if isinstance(scope, Unset):
        json_scope = UNSET
    elif isinstance(scope, GetWorkspacesWorkspaceModelsScopeType0):
        json_scope = scope.value
    else:
        json_scope = scope
    params["scope"] = json_scope

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/workspaces/{workspace}/models".format(
            workspace=quote(str(workspace), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ModelCollection:
    if response.status_code == 200:
        response_200 = ModelCollection.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | ModelCollection]:
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
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    query: str | Unset | None = UNSET,
    provider_id: str | Unset | None = UNSET,
    enabled: bool | Unset | None = UNSET,
    scope: GetWorkspacesWorkspaceModelsScopeType0 | Unset | None = UNSET,
) -> Response[ErrorResponse | ModelCollection]:
    """List Models

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        query (None | str | Unset):
        provider_id (None | str | Unset):
        enabled (bool | None | Unset):
        scope (GetWorkspacesWorkspaceModelsScopeType0 | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ModelCollection]
    """

    kwargs = build_request(
        workspace=workspace,
        limit=limit,
        cursor=cursor,
        query=query,
        provider_id=provider_id,
        enabled=enabled,
        scope=scope,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    workspace: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    query: str | Unset | None = UNSET,
    provider_id: str | Unset | None = UNSET,
    enabled: bool | Unset | None = UNSET,
    scope: GetWorkspacesWorkspaceModelsScopeType0 | Unset | None = UNSET,
) -> ErrorResponse | ModelCollection | None:
    """List Models

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        query (None | str | Unset):
        provider_id (None | str | Unset):
        enabled (bool | None | Unset):
        scope (GetWorkspacesWorkspaceModelsScopeType0 | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ModelCollection
    """

    return sync_detailed(
        workspace=workspace,
        client=client,
        limit=limit,
        cursor=cursor,
        query=query,
        provider_id=provider_id,
        enabled=enabled,
        scope=scope,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    query: str | Unset | None = UNSET,
    provider_id: str | Unset | None = UNSET,
    enabled: bool | Unset | None = UNSET,
    scope: GetWorkspacesWorkspaceModelsScopeType0 | Unset | None = UNSET,
) -> Response[ErrorResponse | ModelCollection]:
    """List Models

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        query (None | str | Unset):
        provider_id (None | str | Unset):
        enabled (bool | None | Unset):
        scope (GetWorkspacesWorkspaceModelsScopeType0 | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ModelCollection]
    """

    kwargs = build_request(
        workspace=workspace,
        limit=limit,
        cursor=cursor,
        query=query,
        provider_id=provider_id,
        enabled=enabled,
        scope=scope,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    query: str | Unset | None = UNSET,
    provider_id: str | Unset | None = UNSET,
    enabled: bool | Unset | None = UNSET,
    scope: GetWorkspacesWorkspaceModelsScopeType0 | Unset | None = UNSET,
) -> ErrorResponse | ModelCollection | None:
    """List Models

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        query (None | str | Unset):
        provider_id (None | str | Unset):
        enabled (bool | None | Unset):
        scope (GetWorkspacesWorkspaceModelsScopeType0 | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ModelCollection
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            client=client,
            limit=limit,
            cursor=cursor,
            query=query,
            provider_id=provider_id,
            enabled=enabled,
            scope=scope,
        )
    ).parsed
