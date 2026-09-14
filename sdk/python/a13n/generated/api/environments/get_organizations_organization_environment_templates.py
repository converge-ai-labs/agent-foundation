from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.collection_environment_template import CollectionEnvironmentTemplate
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def build_request(
    organization: str,
    *,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    label: list[str] | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    json_cursor: str | Unset | None
    if isinstance(cursor, Unset):
        json_cursor = UNSET
    else:
        json_cursor = cursor
    params["cursor"] = json_cursor

    json_label: list[str] | Unset = UNSET
    if not isinstance(label, Unset):
        json_label = label

    params["label"] = json_label

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/organizations/{organization}/environment-templates".format(
            organization=quote(str(organization), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> CollectionEnvironmentTemplate | ErrorResponse:
    if response.status_code == 200:
        response_200 = CollectionEnvironmentTemplate.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[CollectionEnvironmentTemplate | ErrorResponse]:
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
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    label: list[str] | Unset = UNSET,
) -> Response[CollectionEnvironmentTemplate | ErrorResponse]:
    """Organization List Templates

    Args:
        organization (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        label (list[str] | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CollectionEnvironmentTemplate | ErrorResponse]
    """

    kwargs = build_request(
        organization=organization,
        limit=limit,
        cursor=cursor,
        label=label,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    organization: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    label: list[str] | Unset = UNSET,
) -> CollectionEnvironmentTemplate | ErrorResponse | None:
    """Organization List Templates

    Args:
        organization (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        label (list[str] | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CollectionEnvironmentTemplate | ErrorResponse
    """

    return sync_detailed(
        organization=organization,
        client=client,
        limit=limit,
        cursor=cursor,
        label=label,
    ).parsed


async def asyncio_detailed(
    organization: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    label: list[str] | Unset = UNSET,
) -> Response[CollectionEnvironmentTemplate | ErrorResponse]:
    """Organization List Templates

    Args:
        organization (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        label (list[str] | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CollectionEnvironmentTemplate | ErrorResponse]
    """

    kwargs = build_request(
        organization=organization,
        limit=limit,
        cursor=cursor,
        label=label,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    label: list[str] | Unset = UNSET,
) -> CollectionEnvironmentTemplate | ErrorResponse | None:
    """Organization List Templates

    Args:
        organization (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        label (list[str] | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CollectionEnvironmentTemplate | ErrorResponse
    """

    return (
        await asyncio_detailed(
            organization=organization,
            client=client,
            limit=limit,
            cursor=cursor,
            label=label,
        )
    ).parsed
