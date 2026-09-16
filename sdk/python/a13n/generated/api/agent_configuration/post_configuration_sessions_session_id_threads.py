from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.configuration_thread_view import ConfigurationThreadView
from ...models.create_configuration_thread_request import CreateConfigurationThreadRequest
from ...models.error_response import ErrorResponse
from ...types import Response


def build_request(
    session_id: str,
    *,
    body: CreateConfigurationThreadRequest,
    idempotency_key: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/configuration-sessions/{session_id}/threads".format(
            session_id=quote(str(session_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ConfigurationThreadView | ErrorResponse:
    if response.status_code == 201:
        response_201 = ConfigurationThreadView.from_dict(response.json())

        return response_201

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ConfigurationThreadView | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    session_id: str,
    *,
    client: AuthenticatedClient,
    body: CreateConfigurationThreadRequest,
    idempotency_key: str,
) -> Response[ConfigurationThreadView | ErrorResponse]:
    """Create Thread

    Args:
        session_id (str):
        idempotency_key (str):
        body (CreateConfigurationThreadRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConfigurationThreadView | ErrorResponse]
    """

    kwargs = build_request(
        session_id=session_id,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    session_id: str,
    *,
    client: AuthenticatedClient,
    body: CreateConfigurationThreadRequest,
    idempotency_key: str,
) -> ConfigurationThreadView | ErrorResponse | None:
    """Create Thread

    Args:
        session_id (str):
        idempotency_key (str):
        body (CreateConfigurationThreadRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConfigurationThreadView | ErrorResponse
    """

    return sync_detailed(
        session_id=session_id,
        client=client,
        body=body,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    session_id: str,
    *,
    client: AuthenticatedClient,
    body: CreateConfigurationThreadRequest,
    idempotency_key: str,
) -> Response[ConfigurationThreadView | ErrorResponse]:
    """Create Thread

    Args:
        session_id (str):
        idempotency_key (str):
        body (CreateConfigurationThreadRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConfigurationThreadView | ErrorResponse]
    """

    kwargs = build_request(
        session_id=session_id,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    session_id: str,
    *,
    client: AuthenticatedClient,
    body: CreateConfigurationThreadRequest,
    idempotency_key: str,
) -> ConfigurationThreadView | ErrorResponse | None:
    """Create Thread

    Args:
        session_id (str):
        idempotency_key (str):
        body (CreateConfigurationThreadRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConfigurationThreadView | ErrorResponse
    """

    return (
        await asyncio_detailed(
            session_id=session_id,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed
