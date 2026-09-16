from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.configuration_draft import ConfigurationDraft
from ...models.discard_draft_request import DiscardDraftRequest
from ...models.error_response import ErrorResponse
from ...types import Response


def build_request(
    draft_id: str,
    *,
    body: DiscardDraftRequest,
    idempotency_key: str,
    if_match: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["Idempotency-Key"] = idempotency_key

    headers["If-Match"] = if_match

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/configuration-drafts/{draft_id}/discard".format(
            draft_id=quote(str(draft_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ConfigurationDraft | ErrorResponse:
    if response.status_code == 200:
        response_200 = ConfigurationDraft.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ConfigurationDraft | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    draft_id: str,
    *,
    client: AuthenticatedClient,
    body: DiscardDraftRequest,
    idempotency_key: str,
    if_match: str,
) -> Response[ConfigurationDraft | ErrorResponse]:
    """Discard Draft

    Args:
        draft_id (str):
        idempotency_key (str):
        if_match (str):
        body (DiscardDraftRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConfigurationDraft | ErrorResponse]
    """

    kwargs = build_request(
        draft_id=draft_id,
        body=body,
        idempotency_key=idempotency_key,
        if_match=if_match,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    draft_id: str,
    *,
    client: AuthenticatedClient,
    body: DiscardDraftRequest,
    idempotency_key: str,
    if_match: str,
) -> ConfigurationDraft | ErrorResponse | None:
    """Discard Draft

    Args:
        draft_id (str):
        idempotency_key (str):
        if_match (str):
        body (DiscardDraftRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConfigurationDraft | ErrorResponse
    """

    return sync_detailed(
        draft_id=draft_id,
        client=client,
        body=body,
        idempotency_key=idempotency_key,
        if_match=if_match,
    ).parsed


async def asyncio_detailed(
    draft_id: str,
    *,
    client: AuthenticatedClient,
    body: DiscardDraftRequest,
    idempotency_key: str,
    if_match: str,
) -> Response[ConfigurationDraft | ErrorResponse]:
    """Discard Draft

    Args:
        draft_id (str):
        idempotency_key (str):
        if_match (str):
        body (DiscardDraftRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConfigurationDraft | ErrorResponse]
    """

    kwargs = build_request(
        draft_id=draft_id,
        body=body,
        idempotency_key=idempotency_key,
        if_match=if_match,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    draft_id: str,
    *,
    client: AuthenticatedClient,
    body: DiscardDraftRequest,
    idempotency_key: str,
    if_match: str,
) -> ConfigurationDraft | ErrorResponse | None:
    """Discard Draft

    Args:
        draft_id (str):
        idempotency_key (str):
        if_match (str):
        body (DiscardDraftRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConfigurationDraft | ErrorResponse
    """

    return (
        await asyncio_detailed(
            draft_id=draft_id,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
            if_match=if_match,
        )
    ).parsed
