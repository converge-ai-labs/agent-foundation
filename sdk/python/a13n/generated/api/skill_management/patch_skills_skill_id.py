from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.skill import Skill
from ...models.update_skill_request import UpdateSkillRequest
from ...types import Response


def build_request(
    skill_id: str,
    *,
    body: UpdateSkillRequest,
    if_match: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["If-Match"] = if_match

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/api/v1/skills/{skill_id}".format(
            skill_id=quote(str(skill_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> ErrorResponse | Skill:
    if response.status_code == 200:
        response_200 = Skill.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | Skill]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    skill_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateSkillRequest,
    if_match: str,
) -> Response[ErrorResponse | Skill]:
    """Update Skill

    Args:
        skill_id (str):
        if_match (str):
        body (UpdateSkillRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | Skill]
    """

    kwargs = build_request(
        skill_id=skill_id,
        body=body,
        if_match=if_match,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    skill_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateSkillRequest,
    if_match: str,
) -> ErrorResponse | Skill | None:
    """Update Skill

    Args:
        skill_id (str):
        if_match (str):
        body (UpdateSkillRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | Skill
    """

    return sync_detailed(
        skill_id=skill_id,
        client=client,
        body=body,
        if_match=if_match,
    ).parsed


async def asyncio_detailed(
    skill_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateSkillRequest,
    if_match: str,
) -> Response[ErrorResponse | Skill]:
    """Update Skill

    Args:
        skill_id (str):
        if_match (str):
        body (UpdateSkillRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | Skill]
    """

    kwargs = build_request(
        skill_id=skill_id,
        body=body,
        if_match=if_match,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    skill_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateSkillRequest,
    if_match: str,
) -> ErrorResponse | Skill | None:
    """Update Skill

    Args:
        skill_id (str):
        if_match (str):
        body (UpdateSkillRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | Skill
    """

    return (
        await asyncio_detailed(
            skill_id=skill_id,
            client=client,
            body=body,
            if_match=if_match,
        )
    ).parsed
