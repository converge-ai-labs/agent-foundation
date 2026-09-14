from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.labels_body import LabelsBody
from ...types import Response


def build_request(
    template_id: str,
    *,
    body: LabelsBody,
    if_match: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["If-Match"] = if_match

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/api/v1/environment-templates/{template_id}/labels".format(
            template_id=quote(str(template_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> ErrorResponse | LabelsBody:
    if response.status_code == 200:
        response_200 = LabelsBody.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | LabelsBody]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    template_id: str,
    *,
    client: AuthenticatedClient,
    body: LabelsBody,
    if_match: str,
) -> Response[ErrorResponse | LabelsBody]:
    """Put Template Labels

    Args:
        template_id (str):
        if_match (str):
        body (LabelsBody): Complete replacement body for a resource label map.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | LabelsBody]
    """

    kwargs = build_request(
        template_id=template_id,
        body=body,
        if_match=if_match,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    template_id: str,
    *,
    client: AuthenticatedClient,
    body: LabelsBody,
    if_match: str,
) -> ErrorResponse | LabelsBody | None:
    """Put Template Labels

    Args:
        template_id (str):
        if_match (str):
        body (LabelsBody): Complete replacement body for a resource label map.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | LabelsBody
    """

    return sync_detailed(
        template_id=template_id,
        client=client,
        body=body,
        if_match=if_match,
    ).parsed


async def asyncio_detailed(
    template_id: str,
    *,
    client: AuthenticatedClient,
    body: LabelsBody,
    if_match: str,
) -> Response[ErrorResponse | LabelsBody]:
    """Put Template Labels

    Args:
        template_id (str):
        if_match (str):
        body (LabelsBody): Complete replacement body for a resource label map.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | LabelsBody]
    """

    kwargs = build_request(
        template_id=template_id,
        body=body,
        if_match=if_match,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    template_id: str,
    *,
    client: AuthenticatedClient,
    body: LabelsBody,
    if_match: str,
) -> ErrorResponse | LabelsBody | None:
    """Put Template Labels

    Args:
        template_id (str):
        if_match (str):
        body (LabelsBody): Complete replacement body for a resource label map.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | LabelsBody
    """

    return (
        await asyncio_detailed(
            template_id=template_id,
            client=client,
            body=body,
            if_match=if_match,
        )
    ).parsed
