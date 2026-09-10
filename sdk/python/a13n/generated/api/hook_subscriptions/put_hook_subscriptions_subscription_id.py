from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.hook_subscription import HookSubscription
from ...models.update_hook_subscription_request import UpdateHookSubscriptionRequest
from ...types import Response


def build_request(
    subscription_id: str,
    *,
    body: UpdateHookSubscriptionRequest,
    if_match: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    headers["If-Match"] = if_match

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/api/v1/hook-subscriptions/{subscription_id}".format(
            subscription_id=quote(str(subscription_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | HookSubscription:
    if response.status_code == 200:
        response_200 = HookSubscription.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | HookSubscription]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    subscription_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateHookSubscriptionRequest,
    if_match: str,
) -> Response[ErrorResponse | HookSubscription]:
    """Update Hook Subscription

    Args:
        subscription_id (str):
        if_match (str):
        body (UpdateHookSubscriptionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | HookSubscription]
    """

    kwargs = build_request(
        subscription_id=subscription_id,
        body=body,
        if_match=if_match,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    subscription_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateHookSubscriptionRequest,
    if_match: str,
) -> ErrorResponse | HookSubscription | None:
    """Update Hook Subscription

    Args:
        subscription_id (str):
        if_match (str):
        body (UpdateHookSubscriptionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | HookSubscription
    """

    return sync_detailed(
        subscription_id=subscription_id,
        client=client,
        body=body,
        if_match=if_match,
    ).parsed


async def asyncio_detailed(
    subscription_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateHookSubscriptionRequest,
    if_match: str,
) -> Response[ErrorResponse | HookSubscription]:
    """Update Hook Subscription

    Args:
        subscription_id (str):
        if_match (str):
        body (UpdateHookSubscriptionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | HookSubscription]
    """

    kwargs = build_request(
        subscription_id=subscription_id,
        body=body,
        if_match=if_match,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    subscription_id: str,
    *,
    client: AuthenticatedClient,
    body: UpdateHookSubscriptionRequest,
    if_match: str,
) -> ErrorResponse | HookSubscription | None:
    """Update Hook Subscription

    Args:
        subscription_id (str):
        if_match (str):
        body (UpdateHookSubscriptionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | HookSubscription
    """

    return (
        await asyncio_detailed(
            subscription_id=subscription_id,
            client=client,
            body=body,
            if_match=if_match,
        )
    ).parsed
