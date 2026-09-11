from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.observation_collection import ObservationCollection
from ...models.trace_view import TraceView
from ...types import UNSET, Response, Unset


def build_request(
    workspace: str,
    trace_id: str,
    *,
    view: TraceView | Unset = UNSET,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_view: str | Unset = UNSET
    if not isinstance(view, Unset):
        json_view = view.value

    params["view"] = json_view

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
        "url": "/api/v1/workspaces/{workspace}/traces/{trace_id}/observations".format(
            workspace=quote(str(workspace), safe=""),
            trace_id=quote(str(trace_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ObservationCollection:
    if response.status_code == 200:
        response_200 = ObservationCollection.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | ObservationCollection]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    workspace: str,
    trace_id: str,
    *,
    client: AuthenticatedClient,
    view: TraceView | Unset = UNSET,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
) -> Response[ErrorResponse | ObservationCollection]:
    """List Trace Observations

    Args:
        workspace (str):
        trace_id (str):
        view (TraceView | Unset):
        limit (int | Unset):
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ObservationCollection]
    """

    kwargs = build_request(
        workspace=workspace,
        trace_id=trace_id,
        view=view,
        limit=limit,
        cursor=cursor,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    workspace: str,
    trace_id: str,
    *,
    client: AuthenticatedClient,
    view: TraceView | Unset = UNSET,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
) -> ErrorResponse | ObservationCollection | None:
    """List Trace Observations

    Args:
        workspace (str):
        trace_id (str):
        view (TraceView | Unset):
        limit (int | Unset):
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ObservationCollection
    """

    return sync_detailed(
        workspace=workspace,
        trace_id=trace_id,
        client=client,
        view=view,
        limit=limit,
        cursor=cursor,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    trace_id: str,
    *,
    client: AuthenticatedClient,
    view: TraceView | Unset = UNSET,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
) -> Response[ErrorResponse | ObservationCollection]:
    """List Trace Observations

    Args:
        workspace (str):
        trace_id (str):
        view (TraceView | Unset):
        limit (int | Unset):
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ObservationCollection]
    """

    kwargs = build_request(
        workspace=workspace,
        trace_id=trace_id,
        view=view,
        limit=limit,
        cursor=cursor,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    trace_id: str,
    *,
    client: AuthenticatedClient,
    view: TraceView | Unset = UNSET,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
) -> ErrorResponse | ObservationCollection | None:
    """List Trace Observations

    Args:
        workspace (str):
        trace_id (str):
        view (TraceView | Unset):
        limit (int | Unset):
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ObservationCollection
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            trace_id=trace_id,
            client=client,
            view=view,
            limit=limit,
            cursor=cursor,
        )
    ).parsed
