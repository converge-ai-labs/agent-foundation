from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.expected_version import ExpectedVersion
from ...models.invitation import Invitation
from ...types import Response


def build_request(
    invitation_id: str,
    *,
    body: ExpectedVersion,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/invitations/{invitation_id}/revoke".format(
            invitation_id=quote(str(invitation_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> ErrorResponse | Invitation:
    if response.status_code == 200:
        response_200 = Invitation.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | Invitation]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    invitation_id: str,
    *,
    client: AuthenticatedClient,
    body: ExpectedVersion,
) -> Response[ErrorResponse | Invitation]:
    """Revoke Invitation

    Args:
        invitation_id (str):
        body (ExpectedVersion):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | Invitation]
    """

    kwargs = build_request(
        invitation_id=invitation_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    invitation_id: str,
    *,
    client: AuthenticatedClient,
    body: ExpectedVersion,
) -> ErrorResponse | Invitation | None:
    """Revoke Invitation

    Args:
        invitation_id (str):
        body (ExpectedVersion):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | Invitation
    """

    return sync_detailed(
        invitation_id=invitation_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    invitation_id: str,
    *,
    client: AuthenticatedClient,
    body: ExpectedVersion,
) -> Response[ErrorResponse | Invitation]:
    """Revoke Invitation

    Args:
        invitation_id (str):
        body (ExpectedVersion):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | Invitation]
    """

    kwargs = build_request(
        invitation_id=invitation_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    invitation_id: str,
    *,
    client: AuthenticatedClient,
    body: ExpectedVersion,
) -> ErrorResponse | Invitation | None:
    """Revoke Invitation

    Args:
        invitation_id (str):
        body (ExpectedVersion):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | Invitation
    """

    return (
        await asyncio_detailed(
            invitation_id=invitation_id,
            client=client,
            body=body,
        )
    ).parsed
