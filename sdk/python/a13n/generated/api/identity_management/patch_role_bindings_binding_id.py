from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.change_role_request import ChangeRoleRequest
from ...models.error_response import ErrorResponse
from ...models.role_binding import RoleBinding
from ...types import Response


def build_request(
    binding_id: str,
    *,
    body: ChangeRoleRequest,
    if_match: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["If-Match"] = if_match

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/api/v1/role-bindings/{binding_id}".format(
            binding_id=quote(str(binding_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> ErrorResponse | RoleBinding:
    if response.status_code == 200:
        response_200 = RoleBinding.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | RoleBinding]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    binding_id: str,
    *,
    client: AuthenticatedClient,
    body: ChangeRoleRequest,
    if_match: str,
) -> Response[ErrorResponse | RoleBinding]:
    """Change Role

    Args:
        binding_id (str):
        if_match (str):
        body (ChangeRoleRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | RoleBinding]
    """

    kwargs = build_request(
        binding_id=binding_id,
        body=body,
        if_match=if_match,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    binding_id: str,
    *,
    client: AuthenticatedClient,
    body: ChangeRoleRequest,
    if_match: str,
) -> ErrorResponse | RoleBinding | None:
    """Change Role

    Args:
        binding_id (str):
        if_match (str):
        body (ChangeRoleRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | RoleBinding
    """

    return sync_detailed(
        binding_id=binding_id,
        client=client,
        body=body,
        if_match=if_match,
    ).parsed


async def asyncio_detailed(
    binding_id: str,
    *,
    client: AuthenticatedClient,
    body: ChangeRoleRequest,
    if_match: str,
) -> Response[ErrorResponse | RoleBinding]:
    """Change Role

    Args:
        binding_id (str):
        if_match (str):
        body (ChangeRoleRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | RoleBinding]
    """

    kwargs = build_request(
        binding_id=binding_id,
        body=body,
        if_match=if_match,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    binding_id: str,
    *,
    client: AuthenticatedClient,
    body: ChangeRoleRequest,
    if_match: str,
) -> ErrorResponse | RoleBinding | None:
    """Change Role

    Args:
        binding_id (str):
        if_match (str):
        body (ChangeRoleRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | RoleBinding
    """

    return (
        await asyncio_detailed(
            binding_id=binding_id,
            client=client,
            body=body,
            if_match=if_match,
        )
    ).parsed
