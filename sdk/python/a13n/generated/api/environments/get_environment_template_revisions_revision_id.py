from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.environment_template_revision import EnvironmentTemplateRevision
from ...models.error_response import ErrorResponse
from ...types import Response


def build_request(
    revision_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/environment-template-revisions/{revision_id}".format(
            revision_id=quote(str(revision_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> EnvironmentTemplateRevision | ErrorResponse:
    if response.status_code == 200:
        response_200 = EnvironmentTemplateRevision.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[EnvironmentTemplateRevision | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    revision_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[EnvironmentTemplateRevision | ErrorResponse]:
    """Get Revision

    Args:
        revision_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EnvironmentTemplateRevision | ErrorResponse]
    """

    kwargs = build_request(
        revision_id=revision_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    revision_id: str,
    *,
    client: AuthenticatedClient,
) -> EnvironmentTemplateRevision | ErrorResponse | None:
    """Get Revision

    Args:
        revision_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EnvironmentTemplateRevision | ErrorResponse
    """

    return sync_detailed(
        revision_id=revision_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    revision_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[EnvironmentTemplateRevision | ErrorResponse]:
    """Get Revision

    Args:
        revision_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EnvironmentTemplateRevision | ErrorResponse]
    """

    kwargs = build_request(
        revision_id=revision_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    revision_id: str,
    *,
    client: AuthenticatedClient,
) -> EnvironmentTemplateRevision | ErrorResponse | None:
    """Get Revision

    Args:
        revision_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EnvironmentTemplateRevision | ErrorResponse
    """

    return (
        await asyncio_detailed(
            revision_id=revision_id,
            client=client,
        )
    ).parsed
