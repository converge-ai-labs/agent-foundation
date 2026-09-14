from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.connector_tool_page import ConnectorToolPage
from ...models.error_response import ErrorResponse
from ...types import Response


def build_request(
    connector_provider_id: str,
    connector_key: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/connector-providers/{connector_provider_id}/connectors/{connector_key}/tools".format(
            connector_provider_id=quote(str(connector_provider_id), safe=""),
            connector_key=quote(str(connector_key), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ConnectorToolPage | ErrorResponse:
    if response.status_code == 200:
        response_200 = ConnectorToolPage.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ConnectorToolPage | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    connector_provider_id: str,
    connector_key: str,
    *,
    client: AuthenticatedClient,
) -> Response[ConnectorToolPage | ErrorResponse]:
    """Preview Connector Tools

    Args:
        connector_provider_id (str):
        connector_key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConnectorToolPage | ErrorResponse]
    """

    kwargs = build_request(
        connector_provider_id=connector_provider_id,
        connector_key=connector_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    connector_provider_id: str,
    connector_key: str,
    *,
    client: AuthenticatedClient,
) -> ConnectorToolPage | ErrorResponse | None:
    """Preview Connector Tools

    Args:
        connector_provider_id (str):
        connector_key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConnectorToolPage | ErrorResponse
    """

    return sync_detailed(
        connector_provider_id=connector_provider_id,
        connector_key=connector_key,
        client=client,
    ).parsed


async def asyncio_detailed(
    connector_provider_id: str,
    connector_key: str,
    *,
    client: AuthenticatedClient,
) -> Response[ConnectorToolPage | ErrorResponse]:
    """Preview Connector Tools

    Args:
        connector_provider_id (str):
        connector_key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConnectorToolPage | ErrorResponse]
    """

    kwargs = build_request(
        connector_provider_id=connector_provider_id,
        connector_key=connector_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    connector_provider_id: str,
    connector_key: str,
    *,
    client: AuthenticatedClient,
) -> ConnectorToolPage | ErrorResponse | None:
    """Preview Connector Tools

    Args:
        connector_provider_id (str):
        connector_key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConnectorToolPage | ErrorResponse
    """

    return (
        await asyncio_detailed(
            connector_provider_id=connector_provider_id,
            connector_key=connector_key,
            client=client,
        )
    ).parsed
