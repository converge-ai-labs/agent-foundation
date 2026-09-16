from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.document_entry import DocumentEntry
from ...models.error_response import ErrorResponse
from ...types import Response


def build_request(
    account_id: str,
    scope_id: str,
    document_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/operations/{document_id}/reconcile".format(
            account_id=quote(str(account_id), safe=""),
            scope_id=quote(str(scope_id), safe=""),
            document_id=quote(str(document_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> DocumentEntry | ErrorResponse:
    if response.status_code == 200:
        response_200 = DocumentEntry.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[DocumentEntry | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    account_id: str,
    scope_id: str,
    document_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[DocumentEntry | ErrorResponse]:
    """Reconcile Operation

    Args:
        account_id (str):
        scope_id (str):
        document_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DocumentEntry | ErrorResponse]
    """

    kwargs = build_request(
        account_id=account_id,
        scope_id=scope_id,
        document_id=document_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    account_id: str,
    scope_id: str,
    document_id: str,
    *,
    client: AuthenticatedClient,
) -> DocumentEntry | ErrorResponse | None:
    """Reconcile Operation

    Args:
        account_id (str):
        scope_id (str):
        document_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DocumentEntry | ErrorResponse
    """

    return sync_detailed(
        account_id=account_id,
        scope_id=scope_id,
        document_id=document_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    account_id: str,
    scope_id: str,
    document_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[DocumentEntry | ErrorResponse]:
    """Reconcile Operation

    Args:
        account_id (str):
        scope_id (str):
        document_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DocumentEntry | ErrorResponse]
    """

    kwargs = build_request(
        account_id=account_id,
        scope_id=scope_id,
        document_id=document_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    account_id: str,
    scope_id: str,
    document_id: str,
    *,
    client: AuthenticatedClient,
) -> DocumentEntry | ErrorResponse | None:
    """Reconcile Operation

    Args:
        account_id (str):
        scope_id (str):
        document_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DocumentEntry | ErrorResponse
    """

    return (
        await asyncio_detailed(
            account_id=account_id,
            scope_id=scope_id,
            document_id=document_id,
            client=client,
        )
    ).parsed
