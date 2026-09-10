from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...._binary import file_chunks
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.skill_upload_receipt import SkillUploadReceipt
from ...types import File, Response


def build_request(
    workspace: str,
    *,
    body: File,
    idempotency_key: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/workspaces/{workspace}/skill-uploads".format(
            workspace=quote(str(workspace), safe=""),
        ),
    }

    _kwargs["content"] = body.payload
    headers["Content-Type"] = "application/zip"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SkillUploadReceipt:
    if response.status_code == 201:
        response_201 = SkillUploadReceipt.from_dict(response.json())

        return response_201

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | SkillUploadReceipt]:
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
    idempotency_key: str,
) -> Response[ErrorResponse | SkillUploadReceipt]:
    """Stage Skill Upload

    Args:
        workspace (str):
        idempotency_key (str):
        body (File):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SkillUploadReceipt]
    """

    kwargs = build_request(
        workspace=workspace,
        body=body,
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
    idempotency_key: str,
) -> ErrorResponse | SkillUploadReceipt | None:
    """Stage Skill Upload

    Args:
        workspace (str):
        idempotency_key (str):
        body (File):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SkillUploadReceipt
    """

    return sync_detailed(
        workspace=workspace,
        client=client,
        body=body,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    *,
    client: AuthenticatedClient,
    body: File,
    idempotency_key: str,
) -> Response[ErrorResponse | SkillUploadReceipt]:
    """Stage Skill Upload

    Args:
        workspace (str):
        idempotency_key (str):
        body (File):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SkillUploadReceipt]
    """

    kwargs = build_request(
        workspace=workspace,
        body=body,
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
    idempotency_key: str,
) -> ErrorResponse | SkillUploadReceipt | None:
    """Stage Skill Upload

    Args:
        workspace (str):
        idempotency_key (str):
        body (File):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SkillUploadReceipt
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed
