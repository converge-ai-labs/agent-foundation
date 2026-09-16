from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.configuration_draft_review import ConfigurationDraftReview
from ...models.error_response import ErrorResponse
from ...types import Response


def build_request(
    draft_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/configuration-drafts/{draft_id}".format(
            draft_id=quote(str(draft_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ConfigurationDraftReview | ErrorResponse:
    if response.status_code == 200:
        response_200 = ConfigurationDraftReview.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ConfigurationDraftReview | ErrorResponse]:
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
) -> Response[ConfigurationDraftReview | ErrorResponse]:
    """Get Draft

    Args:
        draft_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConfigurationDraftReview | ErrorResponse]
    """

    kwargs = build_request(
        draft_id=draft_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    draft_id: str,
    *,
    client: AuthenticatedClient,
) -> ConfigurationDraftReview | ErrorResponse | None:
    """Get Draft

    Args:
        draft_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConfigurationDraftReview | ErrorResponse
    """

    return sync_detailed(
        draft_id=draft_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    draft_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[ConfigurationDraftReview | ErrorResponse]:
    """Get Draft

    Args:
        draft_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConfigurationDraftReview | ErrorResponse]
    """

    kwargs = build_request(
        draft_id=draft_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    draft_id: str,
    *,
    client: AuthenticatedClient,
) -> ConfigurationDraftReview | ErrorResponse | None:
    """Get Draft

    Args:
        draft_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConfigurationDraftReview | ErrorResponse
    """

    return (
        await asyncio_detailed(
            draft_id=draft_id,
            client=client,
        )
    ).parsed
