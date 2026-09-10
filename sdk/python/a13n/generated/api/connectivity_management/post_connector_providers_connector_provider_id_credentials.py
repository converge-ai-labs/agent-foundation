from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.connector_provider import ConnectorProvider
from ...models.error_response import ErrorResponse
from ...models.replace_connector_provider_credentials_request import ReplaceConnectorProviderCredentialsRequest
from ...types import Response


def build_request(
    connector_provider_id: str,
    *,
    body: ReplaceConnectorProviderCredentialsRequest,
    idempotency_key: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/connector-providers/{connector_provider_id}/credentials".format(
            connector_provider_id=quote(str(connector_provider_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ConnectorProvider | ErrorResponse:
    if response.status_code == 200:
        response_200 = ConnectorProvider.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ConnectorProvider | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    connector_provider_id: str,
    *,
    client: AuthenticatedClient,
    body: ReplaceConnectorProviderCredentialsRequest,
    idempotency_key: str,
) -> Response[ConnectorProvider | ErrorResponse]:
    """Replace Connector Provider Credentials

    Args:
        connector_provider_id (str):
        idempotency_key (str):
        body (ReplaceConnectorProviderCredentialsRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConnectorProvider | ErrorResponse]
    """

    kwargs = build_request(
        connector_provider_id=connector_provider_id,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    connector_provider_id: str,
    *,
    client: AuthenticatedClient,
    body: ReplaceConnectorProviderCredentialsRequest,
    idempotency_key: str,
) -> ConnectorProvider | ErrorResponse | None:
    """Replace Connector Provider Credentials

    Args:
        connector_provider_id (str):
        idempotency_key (str):
        body (ReplaceConnectorProviderCredentialsRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConnectorProvider | ErrorResponse
    """

    return sync_detailed(
        connector_provider_id=connector_provider_id,
        client=client,
        body=body,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    connector_provider_id: str,
    *,
    client: AuthenticatedClient,
    body: ReplaceConnectorProviderCredentialsRequest,
    idempotency_key: str,
) -> Response[ConnectorProvider | ErrorResponse]:
    """Replace Connector Provider Credentials

    Args:
        connector_provider_id (str):
        idempotency_key (str):
        body (ReplaceConnectorProviderCredentialsRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConnectorProvider | ErrorResponse]
    """

    kwargs = build_request(
        connector_provider_id=connector_provider_id,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    connector_provider_id: str,
    *,
    client: AuthenticatedClient,
    body: ReplaceConnectorProviderCredentialsRequest,
    idempotency_key: str,
) -> ConnectorProvider | ErrorResponse | None:
    """Replace Connector Provider Credentials

    Args:
        connector_provider_id (str):
        idempotency_key (str):
        body (ReplaceConnectorProviderCredentialsRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConnectorProvider | ErrorResponse
    """

    return (
        await asyncio_detailed(
            connector_provider_id=connector_provider_id,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed
