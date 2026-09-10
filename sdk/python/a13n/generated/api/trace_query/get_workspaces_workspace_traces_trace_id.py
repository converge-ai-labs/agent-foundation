from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.trace_detail import TraceDetail
from ...models.trace_view import TraceView
from ...types import UNSET, Response, Unset


def build_request(
    workspace: str,
    trace_id: str,
    *,
    view: TraceView | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_view: str | Unset = UNSET
    if not isinstance(view, Unset):
        json_view = view.value

    params["view"] = json_view

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/workspaces/{workspace}/traces/{trace_id}".format(
            workspace=quote(str(workspace), safe=""),
            trace_id=quote(str(trace_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> ErrorResponse | TraceDetail:
    if response.status_code == 200:
        response_200 = TraceDetail.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | TraceDetail]:
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
) -> Response[ErrorResponse | TraceDetail]:
    """Get Trace

    Args:
        workspace (str):
        trace_id (str):
        view (TraceView | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | TraceDetail]
    """

    kwargs = build_request(
        workspace=workspace,
        trace_id=trace_id,
        view=view,
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
) -> ErrorResponse | TraceDetail | None:
    """Get Trace

    Args:
        workspace (str):
        trace_id (str):
        view (TraceView | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | TraceDetail
    """

    return sync_detailed(
        workspace=workspace,
        trace_id=trace_id,
        client=client,
        view=view,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    trace_id: str,
    *,
    client: AuthenticatedClient,
    view: TraceView | Unset = UNSET,
) -> Response[ErrorResponse | TraceDetail]:
    """Get Trace

    Args:
        workspace (str):
        trace_id (str):
        view (TraceView | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | TraceDetail]
    """

    kwargs = build_request(
        workspace=workspace,
        trace_id=trace_id,
        view=view,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    trace_id: str,
    *,
    client: AuthenticatedClient,
    view: TraceView | Unset = UNSET,
) -> ErrorResponse | TraceDetail | None:
    """Get Trace

    Args:
        workspace (str):
        trace_id (str):
        view (TraceView | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | TraceDetail
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            trace_id=trace_id,
            client=client,
            view=view,
        )
    ).parsed
