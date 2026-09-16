import datetime
from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.document_collection import DocumentCollection
from ...models.error_response import ErrorResponse
from ...models.get_application_accounts_account_id_memory_scopes_scope_id_documents_kind_type_0 import (
    GetApplicationAccountsAccountIdMemoryScopesScopeIdDocumentsKindType0,
)
from ...types import UNSET, Response, Unset


def build_request(
    account_id: str,
    scope_id: str,
    *,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    activity_date: datetime.date | Unset | None = UNSET,
    kind: GetApplicationAccountsAccountIdMemoryScopesScopeIdDocumentsKindType0 | Unset | None = UNSET,
    include_shared: bool | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    json_cursor: str | Unset | None
    if isinstance(cursor, Unset):
        json_cursor = UNSET
    else:
        json_cursor = cursor
    params["cursor"] = json_cursor

    json_activity_date: str | Unset | None
    if isinstance(activity_date, Unset):
        json_activity_date = UNSET
    elif isinstance(activity_date, datetime.date):
        json_activity_date = activity_date.isoformat()
    else:
        json_activity_date = activity_date
    params["activity_date"] = json_activity_date

    json_kind: str | Unset | None
    if isinstance(kind, Unset):
        json_kind = UNSET
    elif isinstance(kind, GetApplicationAccountsAccountIdMemoryScopesScopeIdDocumentsKindType0):
        json_kind = kind.value
    else:
        json_kind = kind
    params["kind"] = json_kind

    params["include_shared"] = include_shared

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/documents".format(
            account_id=quote(str(account_id), safe=""),
            scope_id=quote(str(scope_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> DocumentCollection | ErrorResponse:
    if response.status_code == 200:
        response_200 = DocumentCollection.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[DocumentCollection | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    account_id: str,
    scope_id: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    activity_date: datetime.date | Unset | None = UNSET,
    kind: GetApplicationAccountsAccountIdMemoryScopesScopeIdDocumentsKindType0 | Unset | None = UNSET,
    include_shared: bool | Unset = UNSET,
) -> Response[DocumentCollection | ErrorResponse]:
    """Documents

    Args:
        account_id (str):
        scope_id (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        activity_date (datetime.date | None | Unset):
        kind (GetApplicationAccountsAccountIdMemoryScopesScopeIdDocumentsKindType0 | None |
            Unset):
        include_shared (bool | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DocumentCollection | ErrorResponse]
    """

    kwargs = build_request(
        account_id=account_id,
        scope_id=scope_id,
        limit=limit,
        cursor=cursor,
        activity_date=activity_date,
        kind=kind,
        include_shared=include_shared,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    account_id: str,
    scope_id: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    activity_date: datetime.date | Unset | None = UNSET,
    kind: GetApplicationAccountsAccountIdMemoryScopesScopeIdDocumentsKindType0 | Unset | None = UNSET,
    include_shared: bool | Unset = UNSET,
) -> DocumentCollection | ErrorResponse | None:
    """Documents

    Args:
        account_id (str):
        scope_id (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        activity_date (datetime.date | None | Unset):
        kind (GetApplicationAccountsAccountIdMemoryScopesScopeIdDocumentsKindType0 | None |
            Unset):
        include_shared (bool | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DocumentCollection | ErrorResponse
    """

    return sync_detailed(
        account_id=account_id,
        scope_id=scope_id,
        client=client,
        limit=limit,
        cursor=cursor,
        activity_date=activity_date,
        kind=kind,
        include_shared=include_shared,
    ).parsed


async def asyncio_detailed(
    account_id: str,
    scope_id: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    activity_date: datetime.date | Unset | None = UNSET,
    kind: GetApplicationAccountsAccountIdMemoryScopesScopeIdDocumentsKindType0 | Unset | None = UNSET,
    include_shared: bool | Unset = UNSET,
) -> Response[DocumentCollection | ErrorResponse]:
    """Documents

    Args:
        account_id (str):
        scope_id (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        activity_date (datetime.date | None | Unset):
        kind (GetApplicationAccountsAccountIdMemoryScopesScopeIdDocumentsKindType0 | None |
            Unset):
        include_shared (bool | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DocumentCollection | ErrorResponse]
    """

    kwargs = build_request(
        account_id=account_id,
        scope_id=scope_id,
        limit=limit,
        cursor=cursor,
        activity_date=activity_date,
        kind=kind,
        include_shared=include_shared,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    account_id: str,
    scope_id: str,
    *,
    client: AuthenticatedClient,
    limit: int | Unset = UNSET,
    cursor: str | Unset | None = UNSET,
    activity_date: datetime.date | Unset | None = UNSET,
    kind: GetApplicationAccountsAccountIdMemoryScopesScopeIdDocumentsKindType0 | Unset | None = UNSET,
    include_shared: bool | Unset = UNSET,
) -> DocumentCollection | ErrorResponse | None:
    """Documents

    Args:
        account_id (str):
        scope_id (str):
        limit (int | Unset):
        cursor (None | str | Unset):
        activity_date (datetime.date | None | Unset):
        kind (GetApplicationAccountsAccountIdMemoryScopesScopeIdDocumentsKindType0 | None |
            Unset):
        include_shared (bool | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DocumentCollection | ErrorResponse
    """

    return (
        await asyncio_detailed(
            account_id=account_id,
            scope_id=scope_id,
            client=client,
            limit=limit,
            cursor=cursor,
            activity_date=activity_date,
            kind=kind,
            include_shared=include_shared,
        )
    ).parsed
