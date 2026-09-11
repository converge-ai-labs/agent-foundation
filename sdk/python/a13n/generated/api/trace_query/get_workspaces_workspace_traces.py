import datetime
from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.search_in import SearchIn
from ...models.trace_collection import TraceCollection
from ...models.trace_view import TraceView
from ...types import UNSET, Response, Unset


def build_request(
    workspace: str,
    *,
    from_: datetime.datetime | Unset | None = UNSET,
    to: datetime.datetime | Unset | None = UNSET,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    query: str | Unset | None = UNSET,
    search_in: SearchIn | Unset | None = UNSET,
    thread_id: str | Unset | None = UNSET,
    run_id: str | Unset | None = UNSET,
    run_attempt_id: str | Unset | None = UNSET,
    view: TraceView | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_from_: str | Unset | None
    if isinstance(from_, Unset):
        json_from_ = UNSET
    elif isinstance(from_, datetime.datetime):
        json_from_ = from_.isoformat()
    else:
        json_from_ = from_
    params["from"] = json_from_

    json_to: str | Unset | None
    if isinstance(to, Unset):
        json_to = UNSET
    elif isinstance(to, datetime.datetime):
        json_to = to.isoformat()
    else:
        json_to = to
    params["to"] = json_to

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

    json_search_in: str | Unset | None
    if isinstance(search_in, Unset):
        json_search_in = UNSET
    elif isinstance(search_in, SearchIn):
        json_search_in = search_in.value
    else:
        json_search_in = search_in
    params["search_in"] = json_search_in

    json_thread_id: str | Unset | None
    if isinstance(thread_id, Unset):
        json_thread_id = UNSET
    else:
        json_thread_id = thread_id
    params["thread_id"] = json_thread_id

    json_run_id: str | Unset | None
    if isinstance(run_id, Unset):
        json_run_id = UNSET
    else:
        json_run_id = run_id
    params["run_id"] = json_run_id

    json_run_attempt_id: str | Unset | None
    if isinstance(run_attempt_id, Unset):
        json_run_attempt_id = UNSET
    else:
        json_run_attempt_id = run_attempt_id
    params["run_attempt_id"] = json_run_attempt_id

    json_view: str | Unset = UNSET
    if not isinstance(view, Unset):
        json_view = view.value

    params["view"] = json_view

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/workspaces/{workspace}/traces".format(
            workspace=quote(str(workspace), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | TraceCollection:
    if response.status_code == 200:
        response_200 = TraceCollection.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | TraceCollection]:
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
    from_: datetime.datetime | Unset | None = UNSET,
    to: datetime.datetime | Unset | None = UNSET,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    query: str | Unset | None = UNSET,
    search_in: SearchIn | Unset | None = UNSET,
    thread_id: str | Unset | None = UNSET,
    run_id: str | Unset | None = UNSET,
    run_attempt_id: str | Unset | None = UNSET,
    view: TraceView | Unset = UNSET,
) -> Response[ErrorResponse | TraceCollection]:
    """List Traces

    Args:
        workspace (str):
        from_ (datetime.datetime | None | Unset):
        to (datetime.datetime | None | Unset):
        limit (int | Unset):
        cursor (None | str | Unset):
        query (None | str | Unset):
        search_in (None | SearchIn | Unset):
        thread_id (None | str | Unset):
        run_id (None | str | Unset):
        run_attempt_id (None | str | Unset):
        view (TraceView | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | TraceCollection]
    """

    kwargs = build_request(
        workspace=workspace,
        from_=from_,
        to=to,
        limit=limit,
        cursor=cursor,
        query=query,
        search_in=search_in,
        thread_id=thread_id,
        run_id=run_id,
        run_attempt_id=run_attempt_id,
        view=view,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    workspace: str,
    *,
    client: AuthenticatedClient,
    from_: datetime.datetime | Unset | None = UNSET,
    to: datetime.datetime | Unset | None = UNSET,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    query: str | Unset | None = UNSET,
    search_in: SearchIn | Unset | None = UNSET,
    thread_id: str | Unset | None = UNSET,
    run_id: str | Unset | None = UNSET,
    run_attempt_id: str | Unset | None = UNSET,
    view: TraceView | Unset = UNSET,
) -> ErrorResponse | TraceCollection | None:
    """List Traces

    Args:
        workspace (str):
        from_ (datetime.datetime | None | Unset):
        to (datetime.datetime | None | Unset):
        limit (int | Unset):
        cursor (None | str | Unset):
        query (None | str | Unset):
        search_in (None | SearchIn | Unset):
        thread_id (None | str | Unset):
        run_id (None | str | Unset):
        run_attempt_id (None | str | Unset):
        view (TraceView | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | TraceCollection
    """

    return sync_detailed(
        workspace=workspace,
        client=client,
        from_=from_,
        to=to,
        limit=limit,
        cursor=cursor,
        query=query,
        search_in=search_in,
        thread_id=thread_id,
        run_id=run_id,
        run_attempt_id=run_attempt_id,
        view=view,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    *,
    client: AuthenticatedClient,
    from_: datetime.datetime | Unset | None = UNSET,
    to: datetime.datetime | Unset | None = UNSET,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    query: str | Unset | None = UNSET,
    search_in: SearchIn | Unset | None = UNSET,
    thread_id: str | Unset | None = UNSET,
    run_id: str | Unset | None = UNSET,
    run_attempt_id: str | Unset | None = UNSET,
    view: TraceView | Unset = UNSET,
) -> Response[ErrorResponse | TraceCollection]:
    """List Traces

    Args:
        workspace (str):
        from_ (datetime.datetime | None | Unset):
        to (datetime.datetime | None | Unset):
        limit (int | Unset):
        cursor (None | str | Unset):
        query (None | str | Unset):
        search_in (None | SearchIn | Unset):
        thread_id (None | str | Unset):
        run_id (None | str | Unset):
        run_attempt_id (None | str | Unset):
        view (TraceView | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | TraceCollection]
    """

    kwargs = build_request(
        workspace=workspace,
        from_=from_,
        to=to,
        limit=limit,
        cursor=cursor,
        query=query,
        search_in=search_in,
        thread_id=thread_id,
        run_id=run_id,
        run_attempt_id=run_attempt_id,
        view=view,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    *,
    client: AuthenticatedClient,
    from_: datetime.datetime | Unset | None = UNSET,
    to: datetime.datetime | Unset | None = UNSET,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    query: str | Unset | None = UNSET,
    search_in: SearchIn | Unset | None = UNSET,
    thread_id: str | Unset | None = UNSET,
    run_id: str | Unset | None = UNSET,
    run_attempt_id: str | Unset | None = UNSET,
    view: TraceView | Unset = UNSET,
) -> ErrorResponse | TraceCollection | None:
    """List Traces

    Args:
        workspace (str):
        from_ (datetime.datetime | None | Unset):
        to (datetime.datetime | None | Unset):
        limit (int | Unset):
        cursor (None | str | Unset):
        query (None | str | Unset):
        search_in (None | SearchIn | Unset):
        thread_id (None | str | Unset):
        run_id (None | str | Unset):
        run_attempt_id (None | str | Unset):
        view (TraceView | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | TraceCollection
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            client=client,
            from_=from_,
            to=to,
            limit=limit,
            cursor=cursor,
            query=query,
            search_in=search_in,
            thread_id=thread_id,
            run_id=run_id,
            run_attempt_id=run_attempt_id,
            view=view,
        )
    ).parsed
