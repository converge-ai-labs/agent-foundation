from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...._binary import file_chunks
from ...client import AuthenticatedClient, Client
from ...models.asset import Asset
from ...models.error_response import ErrorResponse
from ...types import UNSET, File, Response, Unset


def build_request(
    workspace: str,
    *,
    body: File,
    filename: str,
    media_type: str | Unset | None = UNSET,
    idempotency_key: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["Idempotency-Key"] = idempotency_key

    params: dict[str, Any] = {}

    params["filename"] = filename

    json_media_type: str | Unset | None
    if isinstance(media_type, Unset):
        json_media_type = UNSET
    else:
        json_media_type = media_type
    params["media_type"] = json_media_type

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/workspaces/{workspace}/assets".format(
            workspace=quote(str(workspace), safe=""),
        ),
        "params": params,
    }

    _kwargs["content"] = body.payload
    headers["Content-Type"] = "application/octet-stream"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Asset | ErrorResponse:
    if response.status_code == 201:
        response_201 = Asset.from_dict(response.json())

        return response_201

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Asset | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: File,
    filename: str,
    media_type: str | Unset | None = UNSET,
    idempotency_key: str,
) -> Response[Asset | ErrorResponse]:
    """Upload Asset

    Args:
        workspace (str):
        filename (str):
        media_type (None | str | Unset):
        idempotency_key (str):
        body (File):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Asset | ErrorResponse]
    """

    kwargs = build_request(
        workspace=workspace,
        body=body,
        filename=filename,
        media_type=media_type,
        idempotency_key=idempotency_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: File,
    filename: str,
    media_type: str | Unset | None = UNSET,
    idempotency_key: str,
) -> Asset | ErrorResponse | None:
    """Upload Asset

    Args:
        workspace (str):
        filename (str):
        media_type (None | str | Unset):
        idempotency_key (str):
        body (File):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Asset | ErrorResponse
    """

    return sync_detailed(
        workspace=workspace,
        client=client,
        body=body,
        filename=filename,
        media_type=media_type,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: File,
    filename: str,
    media_type: str | Unset | None = UNSET,
    idempotency_key: str,
) -> Response[Asset | ErrorResponse]:
    """Upload Asset

    Args:
        workspace (str):
        filename (str):
        media_type (None | str | Unset):
        idempotency_key (str):
        body (File):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Asset | ErrorResponse]
    """

    kwargs = build_request(
        workspace=workspace,
        body=body,
        filename=filename,
        media_type=media_type,
        idempotency_key=idempotency_key,
    )

    kwargs["content"] = file_chunks(body.payload)

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: File,
    filename: str,
    media_type: str | Unset | None = UNSET,
    idempotency_key: str,
) -> Asset | ErrorResponse | None:
    """Upload Asset

    Args:
        workspace (str):
        filename (str):
        media_type (None | str | Unset):
        idempotency_key (str):
        body (File):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Asset | ErrorResponse
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            client=client,
            body=body,
            filename=filename,
            media_type=media_type,
            idempotency_key=idempotency_key,
        )
    ).parsed
