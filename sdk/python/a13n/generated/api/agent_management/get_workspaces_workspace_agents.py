from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.agent_collection import AgentCollection
from ...models.agent_source import AgentSource
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def build_request(
    workspace: str,
    *,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    enabled: bool | Unset | None = UNSET,
    source: AgentSource | Unset | None = UNSET,
    include_archived: bool | Unset = UNSET,
    label: list[str] | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    json_cursor: str | Unset | None
    if isinstance(cursor, Unset):
        json_cursor = UNSET
    else:
        json_cursor = cursor
    params["cursor"] = json_cursor

    json_enabled: bool | Unset | None
    if isinstance(enabled, Unset):
        json_enabled = UNSET
    else:
        json_enabled = enabled
    params["enabled"] = json_enabled

    json_source: str | Unset | None
    if isinstance(source, Unset):
        json_source = UNSET
    elif isinstance(source, AgentSource):
        json_source = source.value
    else:
        json_source = source
    params["source"] = json_source

    params["include_archived"] = include_archived

    json_label: list[str] | Unset = UNSET
    if not isinstance(label, Unset):
        json_label = label

    params["label"] = json_label

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/workspaces/{workspace}/agents".format(
            workspace=quote(str(workspace), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AgentCollection | ErrorResponse:
    if response.status_code == 200:
        response_200 = AgentCollection.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[AgentCollection | ErrorResponse]:
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
    enabled: bool | Unset | None = UNSET,
    source: AgentSource | Unset | None = UNSET,
    include_archived: bool | Unset = UNSET,
    label: list[str] | Unset = UNSET,
) -> Response[AgentCollection | ErrorResponse]:
    """List Agents

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        enabled (bool | None | Unset):
        source (AgentSource | None | Unset):
        include_archived (bool | Unset):
        label (list[str] | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentCollection | ErrorResponse]
    """

    kwargs = build_request(
        workspace=workspace,
        limit=limit,
        cursor=cursor,
        enabled=enabled,
        source=source,
        include_archived=include_archived,
        label=label,
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
    enabled: bool | Unset | None = UNSET,
    source: AgentSource | Unset | None = UNSET,
    include_archived: bool | Unset = UNSET,
    label: list[str] | Unset = UNSET,
) -> AgentCollection | ErrorResponse | None:
    """List Agents

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        enabled (bool | None | Unset):
        source (AgentSource | None | Unset):
        include_archived (bool | Unset):
        label (list[str] | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentCollection | ErrorResponse
    """

    return sync_detailed(
        workspace=workspace,
        client=client,
        limit=limit,
        cursor=cursor,
        enabled=enabled,
        source=source,
        include_archived=include_archived,
        label=label,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    enabled: bool | Unset | None = UNSET,
    source: AgentSource | Unset | None = UNSET,
    include_archived: bool | Unset = UNSET,
    label: list[str] | Unset = UNSET,
) -> Response[AgentCollection | ErrorResponse]:
    """List Agents

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        enabled (bool | None | Unset):
        source (AgentSource | None | Unset):
        include_archived (bool | Unset):
        label (list[str] | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentCollection | ErrorResponse]
    """

    kwargs = build_request(
        workspace=workspace,
        limit=limit,
        cursor=cursor,
        enabled=enabled,
        source=source,
        include_archived=include_archived,
        label=label,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    enabled: bool | Unset | None = UNSET,
    source: AgentSource | Unset | None = UNSET,
    include_archived: bool | Unset = UNSET,
    label: list[str] | Unset = UNSET,
) -> AgentCollection | ErrorResponse | None:
    """List Agents

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        enabled (bool | None | Unset):
        source (AgentSource | None | Unset):
        include_archived (bool | Unset):
        label (list[str] | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentCollection | ErrorResponse
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            client=client,
            limit=limit,
            cursor=cursor,
            enabled=enabled,
            source=source,
            include_archived=include_archived,
            label=label,
        )
    ).parsed
