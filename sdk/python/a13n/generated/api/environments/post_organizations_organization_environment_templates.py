from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.create_template_request import CreateTemplateRequest
from ...models.environment_template import EnvironmentTemplate
from ...models.error_response import ErrorResponse
from ...types import Response


def build_request(
    organization: str,
    *,
    body: CreateTemplateRequest,
    idempotency_key: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/organizations/{organization}/environment-templates".format(
            organization=quote(str(organization), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> EnvironmentTemplate | ErrorResponse:
    if response.status_code == 201:
        response_201 = EnvironmentTemplate.from_dict(response.json())

        return response_201

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[EnvironmentTemplate | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    organization: str,
    *,
    client: AuthenticatedClient,
    body: CreateTemplateRequest,
    idempotency_key: str,
) -> Response[EnvironmentTemplate | ErrorResponse]:
    """Organization Create Template

    Args:
        organization (str):
        idempotency_key (str):
        body (CreateTemplateRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EnvironmentTemplate | ErrorResponse]
    """

    kwargs = build_request(
        organization=organization,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    organization: str,
    *,
    client: AuthenticatedClient,
    body: CreateTemplateRequest,
    idempotency_key: str,
) -> EnvironmentTemplate | ErrorResponse | None:
    """Organization Create Template

    Args:
        organization (str):
        idempotency_key (str):
        body (CreateTemplateRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EnvironmentTemplate | ErrorResponse
    """

    return sync_detailed(
        organization=organization,
        client=client,
        body=body,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    organization: str,
    *,
    client: AuthenticatedClient,
    body: CreateTemplateRequest,
    idempotency_key: str,
) -> Response[EnvironmentTemplate | ErrorResponse]:
    """Organization Create Template

    Args:
        organization (str):
        idempotency_key (str):
        body (CreateTemplateRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EnvironmentTemplate | ErrorResponse]
    """

    kwargs = build_request(
        organization=organization,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization: str,
    *,
    client: AuthenticatedClient,
    body: CreateTemplateRequest,
    idempotency_key: str,
) -> EnvironmentTemplate | ErrorResponse | None:
    """Organization Create Template

    Args:
        organization (str):
        idempotency_key (str):
        body (CreateTemplateRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EnvironmentTemplate | ErrorResponse
    """

    return (
        await asyncio_detailed(
            organization=organization,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed
