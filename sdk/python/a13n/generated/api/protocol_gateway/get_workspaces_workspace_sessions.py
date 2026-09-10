import datetime
from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.run_status import RunStatus
from ...models.session_collection import SessionCollection
from ...types import UNSET, Response, Unset


def build_request(
    workspace: str,
    *,
    q: str | Unset | None = UNSET,
    agent_id: str | Unset | None = UNSET,
    status: list[RunStatus] | Unset = UNSET,
    trigger_type: list[str] | Unset = UNSET,
    updated_after: datetime.datetime | Unset | None = UNSET,
    updated_before: datetime.datetime | Unset | None = UNSET,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_q: str | Unset | None
    if isinstance(q, Unset):
        json_q = UNSET
    else:
        json_q = q
    params["q"] = json_q

    json_agent_id: str | Unset | None
    if isinstance(agent_id, Unset):
        json_agent_id = UNSET
    else:
        json_agent_id = agent_id
    params["agent_id"] = json_agent_id

    json_status: list[str] | Unset = UNSET
    if not isinstance(status, Unset):
        json_status = []
        for status_item_data in status:
            status_item = status_item_data.value
            json_status.append(status_item)

    params["status"] = json_status

    json_trigger_type: list[str] | Unset = UNSET
    if not isinstance(trigger_type, Unset):
        json_trigger_type = trigger_type

    params["trigger_type"] = json_trigger_type

    json_updated_after: str | Unset | None
    if isinstance(updated_after, Unset):
        json_updated_after = UNSET
    elif isinstance(updated_after, datetime.datetime):
        json_updated_after = updated_after.isoformat()
    else:
        json_updated_after = updated_after
    params["updated_after"] = json_updated_after

    json_updated_before: str | Unset | None
    if isinstance(updated_before, Unset):
        json_updated_before = UNSET
    elif isinstance(updated_before, datetime.datetime):
        json_updated_before = updated_before.isoformat()
    else:
        json_updated_before = updated_before
    params["updated_before"] = json_updated_before

    params["limit"] = limit

    json_cursor: str | Unset | None
    if isinstance(cursor, Unset):
        json_cursor = UNSET
    else:
        json_cursor = cursor
    params["cursor"] = json_cursor

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/workspaces/{workspace}/sessions".format(
            workspace=quote(str(workspace), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SessionCollection:
    if response.status_code == 200:
        response_200 = SessionCollection.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | SessionCollection]:
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
    q: str | Unset | None = UNSET,
    agent_id: str | Unset | None = UNSET,
    status: list[RunStatus] | Unset = UNSET,
    trigger_type: list[str] | Unset = UNSET,
    updated_after: datetime.datetime | Unset | None = UNSET,
    updated_before: datetime.datetime | Unset | None = UNSET,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
) -> Response[ErrorResponse | SessionCollection]:
    """List Sessions

    Args:
        workspace (str):
        q (None | str | Unset):
        agent_id (None | str | Unset):
        status (list[RunStatus] | Unset):
        trigger_type (list[str] | Unset):
        updated_after (datetime.datetime | None | Unset):
        updated_before (datetime.datetime | None | Unset):
        limit (int | Unset):
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SessionCollection]
    """

    kwargs = build_request(
        workspace=workspace,
        q=q,
        agent_id=agent_id,
        status=status,
        trigger_type=trigger_type,
        updated_after=updated_after,
        updated_before=updated_before,
        limit=limit,
        cursor=cursor,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    workspace: str,
    *,
    client: AuthenticatedClient,
    q: str | Unset | None = UNSET,
    agent_id: str | Unset | None = UNSET,
    status: list[RunStatus] | Unset = UNSET,
    trigger_type: list[str] | Unset = UNSET,
    updated_after: datetime.datetime | Unset | None = UNSET,
    updated_before: datetime.datetime | Unset | None = UNSET,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
) -> ErrorResponse | SessionCollection | None:
    """List Sessions

    Args:
        workspace (str):
        q (None | str | Unset):
        agent_id (None | str | Unset):
        status (list[RunStatus] | Unset):
        trigger_type (list[str] | Unset):
        updated_after (datetime.datetime | None | Unset):
        updated_before (datetime.datetime | None | Unset):
        limit (int | Unset):
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SessionCollection
    """

    return sync_detailed(
        workspace=workspace,
        client=client,
        q=q,
        agent_id=agent_id,
        status=status,
        trigger_type=trigger_type,
        updated_after=updated_after,
        updated_before=updated_before,
        limit=limit,
        cursor=cursor,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    *,
    client: AuthenticatedClient,
    q: str | Unset | None = UNSET,
    agent_id: str | Unset | None = UNSET,
    status: list[RunStatus] | Unset = UNSET,
    trigger_type: list[str] | Unset = UNSET,
    updated_after: datetime.datetime | Unset | None = UNSET,
    updated_before: datetime.datetime | Unset | None = UNSET,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
) -> Response[ErrorResponse | SessionCollection]:
    """List Sessions

    Args:
        workspace (str):
        q (None | str | Unset):
        agent_id (None | str | Unset):
        status (list[RunStatus] | Unset):
        trigger_type (list[str] | Unset):
        updated_after (datetime.datetime | None | Unset):
        updated_before (datetime.datetime | None | Unset):
        limit (int | Unset):
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SessionCollection]
    """

    kwargs = build_request(
        workspace=workspace,
        q=q,
        agent_id=agent_id,
        status=status,
        trigger_type=trigger_type,
        updated_after=updated_after,
        updated_before=updated_before,
        limit=limit,
        cursor=cursor,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    *,
    client: AuthenticatedClient,
    q: str | Unset | None = UNSET,
    agent_id: str | Unset | None = UNSET,
    status: list[RunStatus] | Unset = UNSET,
    trigger_type: list[str] | Unset = UNSET,
    updated_after: datetime.datetime | Unset | None = UNSET,
    updated_before: datetime.datetime | Unset | None = UNSET,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
) -> ErrorResponse | SessionCollection | None:
    """List Sessions

    Args:
        workspace (str):
        q (None | str | Unset):
        agent_id (None | str | Unset):
        status (list[RunStatus] | Unset):
        trigger_type (list[str] | Unset):
        updated_after (datetime.datetime | None | Unset):
        updated_before (datetime.datetime | None | Unset):
        limit (int | Unset):
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SessionCollection
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            client=client,
            q=q,
            agent_id=agent_id,
            status=status,
            trigger_type=trigger_type,
            updated_after=updated_after,
            updated_before=updated_before,
            limit=limit,
            cursor=cursor,
        )
    ).parsed
