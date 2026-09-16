from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.bot_collection import BotCollection
from ...models.error_response import ErrorResponse
from ...models.get_workspaces_workspace_bots_condition_type_0 import GetWorkspacesWorkspaceBotsConditionType0
from ...models.get_workspaces_workspace_bots_platform_type_0 import GetWorkspacesWorkspaceBotsPlatformType0
from ...types import UNSET, Response, Unset


def build_request(
    workspace: str,
    *,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    platform: GetWorkspacesWorkspaceBotsPlatformType0 | Unset | None = UNSET,
    condition: GetWorkspacesWorkspaceBotsConditionType0 | Unset | None = UNSET,
    search: str | Unset | None = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    json_cursor: str | Unset | None
    if isinstance(cursor, Unset):
        json_cursor = UNSET
    else:
        json_cursor = cursor
    params["cursor"] = json_cursor

    json_platform: str | Unset | None
    if isinstance(platform, Unset):
        json_platform = UNSET
    elif isinstance(platform, GetWorkspacesWorkspaceBotsPlatformType0):
        json_platform = platform.value
    else:
        json_platform = platform
    params["platform"] = json_platform

    json_condition: str | Unset | None
    if isinstance(condition, Unset):
        json_condition = UNSET
    elif isinstance(condition, GetWorkspacesWorkspaceBotsConditionType0):
        json_condition = condition.value
    else:
        json_condition = condition
    params["condition"] = json_condition

    json_search: str | Unset | None
    if isinstance(search, Unset):
        json_search = UNSET
    else:
        json_search = search
    params["search"] = json_search

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/workspaces/{workspace}/bots".format(
            workspace=quote(str(workspace), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> BotCollection | ErrorResponse:
    if response.status_code == 200:
        response_200 = BotCollection.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[BotCollection | ErrorResponse]:
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
    platform: GetWorkspacesWorkspaceBotsPlatformType0 | Unset | None = UNSET,
    condition: GetWorkspacesWorkspaceBotsConditionType0 | Unset | None = UNSET,
    search: str | Unset | None = UNSET,
) -> Response[BotCollection | ErrorResponse]:
    """Bot Collection

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        platform (GetWorkspacesWorkspaceBotsPlatformType0 | None | Unset):
        condition (GetWorkspacesWorkspaceBotsConditionType0 | None | Unset):
        search (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[BotCollection | ErrorResponse]
    """

    kwargs = build_request(
        workspace=workspace,
        limit=limit,
        cursor=cursor,
        platform=platform,
        condition=condition,
        search=search,
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
    platform: GetWorkspacesWorkspaceBotsPlatformType0 | Unset | None = UNSET,
    condition: GetWorkspacesWorkspaceBotsConditionType0 | Unset | None = UNSET,
    search: str | Unset | None = UNSET,
) -> BotCollection | ErrorResponse | None:
    """Bot Collection

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        platform (GetWorkspacesWorkspaceBotsPlatformType0 | None | Unset):
        condition (GetWorkspacesWorkspaceBotsConditionType0 | None | Unset):
        search (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        BotCollection | ErrorResponse
    """

    return sync_detailed(
        workspace=workspace,
        client=client,
        limit=limit,
        cursor=cursor,
        platform=platform,
        condition=condition,
        search=search,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    platform: GetWorkspacesWorkspaceBotsPlatformType0 | Unset | None = UNSET,
    condition: GetWorkspacesWorkspaceBotsConditionType0 | Unset | None = UNSET,
    search: str | Unset | None = UNSET,
) -> Response[BotCollection | ErrorResponse]:
    """Bot Collection

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        platform (GetWorkspacesWorkspaceBotsPlatformType0 | None | Unset):
        condition (GetWorkspacesWorkspaceBotsConditionType0 | None | Unset):
        search (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[BotCollection | ErrorResponse]
    """

    kwargs = build_request(
        workspace=workspace,
        limit=limit,
        cursor=cursor,
        platform=platform,
        condition=condition,
        search=search,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    platform: GetWorkspacesWorkspaceBotsPlatformType0 | Unset | None = UNSET,
    condition: GetWorkspacesWorkspaceBotsConditionType0 | Unset | None = UNSET,
    search: str | Unset | None = UNSET,
) -> BotCollection | ErrorResponse | None:
    """Bot Collection

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        platform (GetWorkspacesWorkspaceBotsPlatformType0 | None | Unset):
        condition (GetWorkspacesWorkspaceBotsConditionType0 | None | Unset):
        search (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        BotCollection | ErrorResponse
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            client=client,
            limit=limit,
            cursor=cursor,
            platform=platform,
            condition=condition,
            search=search,
        )
    ).parsed
