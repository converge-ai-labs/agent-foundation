from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.get_workspaces_workspace_skills_source_kind_type_0 import GetWorkspacesWorkspaceSkillsSourceKindType0
from ...models.skill_collection import SkillCollection
from ...types import UNSET, Response, Unset


def build_request(
    workspace: str,
    *,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    q: str | Unset | None = UNSET,
    source_kind: GetWorkspacesWorkspaceSkillsSourceKindType0 | Unset | None = UNSET,
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

    json_q: str | Unset | None
    if isinstance(q, Unset):
        json_q = UNSET
    else:
        json_q = q
    params["q"] = json_q

    json_source_kind: str | Unset | None
    if isinstance(source_kind, Unset):
        json_source_kind = UNSET
    elif isinstance(source_kind, GetWorkspacesWorkspaceSkillsSourceKindType0):
        json_source_kind = source_kind.value
    else:
        json_source_kind = source_kind
    params["source_kind"] = json_source_kind

    json_label: list[str] | Unset = UNSET
    if not isinstance(label, Unset):
        json_label = label

    params["label"] = json_label

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/workspaces/{workspace}/skills".format(
            workspace=quote(str(workspace), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SkillCollection:
    if response.status_code == 200:
        response_200 = SkillCollection.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | SkillCollection]:
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
    q: str | Unset | None = UNSET,
    source_kind: GetWorkspacesWorkspaceSkillsSourceKindType0 | Unset | None = UNSET,
    label: list[str] | Unset = UNSET,
) -> Response[ErrorResponse | SkillCollection]:
    """List Skills

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        q (None | str | Unset):
        source_kind (GetWorkspacesWorkspaceSkillsSourceKindType0 | None | Unset):
        label (list[str] | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SkillCollection]
    """

    kwargs = build_request(
        workspace=workspace,
        limit=limit,
        cursor=cursor,
        q=q,
        source_kind=source_kind,
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
    q: str | Unset | None = UNSET,
    source_kind: GetWorkspacesWorkspaceSkillsSourceKindType0 | Unset | None = UNSET,
    label: list[str] | Unset = UNSET,
) -> ErrorResponse | SkillCollection | None:
    """List Skills

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        q (None | str | Unset):
        source_kind (GetWorkspacesWorkspaceSkillsSourceKindType0 | None | Unset):
        label (list[str] | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SkillCollection
    """

    return sync_detailed(
        workspace=workspace,
        client=client,
        limit=limit,
        cursor=cursor,
        q=q,
        source_kind=source_kind,
        label=label,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    q: str | Unset | None = UNSET,
    source_kind: GetWorkspacesWorkspaceSkillsSourceKindType0 | Unset | None = UNSET,
    label: list[str] | Unset = UNSET,
) -> Response[ErrorResponse | SkillCollection]:
    """List Skills

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        q (None | str | Unset):
        source_kind (GetWorkspacesWorkspaceSkillsSourceKindType0 | None | Unset):
        label (list[str] | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SkillCollection]
    """

    kwargs = build_request(
        workspace=workspace,
        limit=limit,
        cursor=cursor,
        q=q,
        source_kind=source_kind,
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
    q: str | Unset | None = UNSET,
    source_kind: GetWorkspacesWorkspaceSkillsSourceKindType0 | Unset | None = UNSET,
    label: list[str] | Unset = UNSET,
) -> ErrorResponse | SkillCollection | None:
    """List Skills

    Args:
        workspace (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        q (None | str | Unset):
        source_kind (GetWorkspacesWorkspaceSkillsSourceKindType0 | None | Unset):
        label (list[str] | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SkillCollection
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            client=client,
            limit=limit,
            cursor=cursor,
            q=q,
            source_kind=source_kind,
            label=label,
        )
    ).parsed
