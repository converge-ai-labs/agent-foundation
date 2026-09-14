from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.environment_command import EnvironmentCommand
from ...models.error_response import ErrorResponse
from ...types import Response


def build_request(
    command_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/environment-commands/{command_id}".format(
            command_id=quote(str(command_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> EnvironmentCommand | ErrorResponse:
    if response.status_code == 200:
        response_200 = EnvironmentCommand.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[EnvironmentCommand | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    command_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[EnvironmentCommand | ErrorResponse]:
    """Get Command

    Args:
        command_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EnvironmentCommand | ErrorResponse]
    """

    kwargs = build_request(
        command_id=command_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    command_id: str,
    *,
    client: AuthenticatedClient,
) -> EnvironmentCommand | ErrorResponse | None:
    """Get Command

    Args:
        command_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EnvironmentCommand | ErrorResponse
    """

    return sync_detailed(
        command_id=command_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    command_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[EnvironmentCommand | ErrorResponse]:
    """Get Command

    Args:
        command_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EnvironmentCommand | ErrorResponse]
    """

    kwargs = build_request(
        command_id=command_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    command_id: str,
    *,
    client: AuthenticatedClient,
) -> EnvironmentCommand | ErrorResponse | None:
    """Get Command

    Args:
        command_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EnvironmentCommand | ErrorResponse
    """

    return (
        await asyncio_detailed(
            command_id=command_id,
            client=client,
        )
    ).parsed
