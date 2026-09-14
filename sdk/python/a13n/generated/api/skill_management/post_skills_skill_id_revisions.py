from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.create_skill_revision_request import CreateSkillRevisionRequest
from ...models.error_response import ErrorResponse
from ...models.skill_publication_receipt import SkillPublicationReceipt
from ...types import Response


def build_request(
    skill_id: str,
    *,
    body: CreateSkillRevisionRequest,
    idempotency_key: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/skills/{skill_id}/revisions".format(
            skill_id=quote(str(skill_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SkillPublicationReceipt:
    if response.status_code == 201:
        response_201 = SkillPublicationReceipt.from_dict(response.json())

        return response_201

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | SkillPublicationReceipt]:
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
    body: CreateSkillRevisionRequest,
    idempotency_key: str,
) -> Response[ErrorResponse | SkillPublicationReceipt]:
    """Create Skill Revision

    Args:
        skill_id (str):
        idempotency_key (str):
        body (CreateSkillRevisionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SkillPublicationReceipt]
    """

    kwargs = build_request(
        skill_id=skill_id,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    skill_id: str,
    *,
    client: AuthenticatedClient,
    body: CreateSkillRevisionRequest,
    idempotency_key: str,
) -> ErrorResponse | SkillPublicationReceipt | None:
    """Create Skill Revision

    Args:
        skill_id (str):
        idempotency_key (str):
        body (CreateSkillRevisionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SkillPublicationReceipt
    """

    return sync_detailed(
        skill_id=skill_id,
        client=client,
        body=body,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    skill_id: str,
    *,
    client: AuthenticatedClient,
    body: CreateSkillRevisionRequest,
    idempotency_key: str,
) -> Response[ErrorResponse | SkillPublicationReceipt]:
    """Create Skill Revision

    Args:
        skill_id (str):
        idempotency_key (str):
        body (CreateSkillRevisionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SkillPublicationReceipt]
    """

    kwargs = build_request(
        skill_id=skill_id,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    skill_id: str,
    *,
    client: AuthenticatedClient,
    body: CreateSkillRevisionRequest,
    idempotency_key: str,
) -> ErrorResponse | SkillPublicationReceipt | None:
    """Create Skill Revision

    Args:
        skill_id (str):
        idempotency_key (str):
        body (CreateSkillRevisionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SkillPublicationReceipt
    """

    return (
        await asyncio_detailed(
            skill_id=skill_id,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed
