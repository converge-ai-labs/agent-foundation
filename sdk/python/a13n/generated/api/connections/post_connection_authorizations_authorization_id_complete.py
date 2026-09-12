from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.authorization import Authorization
from ...models.complete_authorization_request import CompleteAuthorizationRequest
from ...models.error_response import ErrorResponse
from ...types import Response


def build_request(
    authorization_id: str,
    *,
    body: CompleteAuthorizationRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/connection-authorizations/{authorization_id}/complete".format(
            authorization_id=quote(str(authorization_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Authorization | ErrorResponse:
    if response.status_code == 200:
        response_200 = Authorization.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Authorization | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    authorization_id: str,
    *,
    client: AuthenticatedClient,
    body: CompleteAuthorizationRequest,
) -> Response[Authorization | ErrorResponse]:
    """Complete Connection Authorization

    Args:
        authorization_id (str):
        body (CompleteAuthorizationRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Authorization | ErrorResponse]
    """

    kwargs = build_request(
        authorization_id=authorization_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    authorization_id: str,
    *,
    client: AuthenticatedClient,
    body: CompleteAuthorizationRequest,
) -> Authorization | ErrorResponse | None:
    """Complete Connection Authorization

    Args:
        authorization_id (str):
        body (CompleteAuthorizationRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Authorization | ErrorResponse
    """

    return sync_detailed(
        authorization_id=authorization_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    authorization_id: str,
    *,
    client: AuthenticatedClient,
    body: CompleteAuthorizationRequest,
) -> Response[Authorization | ErrorResponse]:
    """Complete Connection Authorization

    Args:
        authorization_id (str):
        body (CompleteAuthorizationRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Authorization | ErrorResponse]
    """

    kwargs = build_request(
        authorization_id=authorization_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    authorization_id: str,
    *,
    client: AuthenticatedClient,
    body: CompleteAuthorizationRequest,
) -> Authorization | ErrorResponse | None:
    """Complete Connection Authorization

    Args:
        authorization_id (str):
        body (CompleteAuthorizationRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Authorization | ErrorResponse
    """

    return (
        await asyncio_detailed(
            authorization_id=authorization_id,
            client=client,
            body=body,
        )
    ).parsed
