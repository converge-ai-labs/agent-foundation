from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.skill_revision import SkillRevision
from ...types import Response


def build_request(
    skill_revision_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/skill-revisions/{skill_revision_id}".format(
            skill_revision_id=quote(str(skill_revision_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> ErrorResponse | SkillRevision:
    if response.status_code == 200:
        response_200 = SkillRevision.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | SkillRevision]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    skill_revision_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[ErrorResponse | SkillRevision]:
    """Get Skill Revision

    Args:
        skill_revision_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SkillRevision]
    """

    kwargs = build_request(
        skill_revision_id=skill_revision_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    skill_revision_id: str,
    *,
    client: AuthenticatedClient,
) -> ErrorResponse | SkillRevision | None:
    """Get Skill Revision

    Args:
        skill_revision_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SkillRevision
    """

    return sync_detailed(
        skill_revision_id=skill_revision_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    skill_revision_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[ErrorResponse | SkillRevision]:
    """Get Skill Revision

    Args:
        skill_revision_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SkillRevision]
    """

    kwargs = build_request(
        skill_revision_id=skill_revision_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    skill_revision_id: str,
    *,
    client: AuthenticatedClient,
) -> ErrorResponse | SkillRevision | None:
    """Get Skill Revision

    Args:
        skill_revision_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SkillRevision
    """

    return (
        await asyncio_detailed(
            skill_revision_id=skill_revision_id,
            client=client,
        )
    ).parsed
