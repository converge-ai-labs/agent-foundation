from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.model import Model
from ...types import Response


def build_request(
    workspace: str,
    model_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/workspaces/{workspace}/models/{model_id}".format(
            workspace=quote(str(workspace), safe=""),
            model_id=quote(str(model_id), safe=""),
        ),
    }

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
) -> Response[ErrorResponse | Model]:
    """Get Model

    Args:
        workspace (str):
        model_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | Model]
    """

    kwargs = build_request(
        workspace=workspace,
        model_id=model_id,
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
) -> ErrorResponse | Model | None:
    """Get Model

    Args:
        workspace (str):
        model_id (str):

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
    ).parsed


async def asyncio_detailed(
    workspace: str,
    model_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[ErrorResponse | Model]:
    """Get Model

    Args:
        workspace (str):
        model_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | Model]
    """

    kwargs = build_request(
        workspace=workspace,
        model_id=model_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    model_id: str,
    *,
    client: AuthenticatedClient,
) -> ErrorResponse | Model | None:
    """Get Model

    Args:
        workspace (str):
        model_id (str):

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
        )
    ).parsed
