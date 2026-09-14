from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.model import Model
from ...models.update_model_request import UpdateModelRequest
from ...types import Response


def build_request(
    workspace: str,
    model_id: str,
    *,
    body: UpdateModelRequest,
    if_match: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["If-Match"] = if_match

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/api/v1/workspaces/{workspace}/models/{model_id}".format(
            workspace=quote(str(workspace), safe=""),
            model_id=quote(str(model_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> ErrorResponse | Model:
    if response.status_code == 200:
        response_200 = Model.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | Model]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    workspace: str,
    model_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateModelRequest,
    if_match: str,
) -> Response[ErrorResponse | Model]:
    """Update Model

    Args:
        workspace (str):
        model_id (str):
        if_match (str):
        body (UpdateModelRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | Model]
    """

    kwargs = build_request(
        workspace=workspace,
        model_id=model_id,
        body=body,
        if_match=if_match,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    workspace: str,
    model_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateModelRequest,
    if_match: str,
) -> ErrorResponse | Model | None:
    """Update Model

    Args:
        workspace (str):
        model_id (str):
        if_match (str):
        body (UpdateModelRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | Model
    """

    return sync_detailed(
        workspace=workspace,
        model_id=model_id,
        client=client,
        body=body,
        if_match=if_match,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    model_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateModelRequest,
    if_match: str,
) -> Response[ErrorResponse | Model]:
    """Update Model

    Args:
        workspace (str):
        model_id (str):
        if_match (str):
        body (UpdateModelRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | Model]
    """

    kwargs = build_request(
        workspace=workspace,
        model_id=model_id,
        body=body,
        if_match=if_match,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    model_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateModelRequest,
    if_match: str,
) -> ErrorResponse | Model | None:
    """Update Model

    Args:
        workspace (str):
        model_id (str):
        if_match (str):
        body (UpdateModelRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | Model
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            model_id=model_id,
            client=client,
            body=body,
            if_match=if_match,
        )
    ).parsed
