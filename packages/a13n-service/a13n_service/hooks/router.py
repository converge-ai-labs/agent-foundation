"""Managed durable HookSubscription API routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from a13n_service.application_errors import ErrorCategory
from a13n_service.etags import resource_etag
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.request_runtime import get_control_runtime

from .domain import (
    CreateHookSubscriptionRequest,
    HookSubscription,
    HookSubscriptionCollection,
    UpdateHookSubscriptionRequest,
    UpdateHookSubscriptionStateRequest,
)
from .errors import HookManagementError
from .management import HookSubscriptionService

router = APIRouter(prefix="/api/v1", tags=["hook-subscriptions"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=1, max_length=256)]


def _service(request: Request) -> HookSubscriptionService:
    control = get_control_runtime(request)
    if control is None:
        raise HookManagementError(
            "hook_management_unavailable",
            "Hook subscription management is unavailable.",
            category=ErrorCategory.unavailable,
        )
    return control.hook_subscriptions


def _set_etag(response: Response, subscription: HookSubscription) -> None:
    response.headers["ETag"] = resource_etag(subscription.id, subscription.updated_at)


@router.get(
    "/workspaces/{workspace_id}/hook-subscriptions",
    response_model=HookSubscriptionCollection,
)
async def list_hook_subscriptions(
    request: Request,
    actor: Actor,
    workspace_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> HookSubscriptionCollection:
    return await _service(request).list(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
    )


@router.post(
    "/workspaces/{workspace_id}/hook-subscriptions",
    response_model=HookSubscription,
    status_code=status.HTTP_201_CREATED,
)
async def create_hook_subscription(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
    body: CreateHookSubscriptionRequest,
) -> HookSubscription:
    subscription = await _service(request).create(
        actor=actor,
        workspace_id=workspace_id,
        request=body,
    )
    _set_etag(response, subscription)
    return subscription


@router.get("/hook-subscriptions/{subscription_id}", response_model=HookSubscription)
async def get_hook_subscription(
    request: Request,
    response: Response,
    actor: Actor,
    subscription_id: str,
) -> HookSubscription:
    subscription = await _service(request).get(actor=actor, subscription_id=subscription_id)
    _set_etag(response, subscription)
    return subscription


@router.put("/hook-subscriptions/{subscription_id}", response_model=HookSubscription)
async def update_hook_subscription(
    request: Request,
    response: Response,
    actor: Actor,
    subscription_id: str,
    body: UpdateHookSubscriptionRequest,
    if_match: IfMatch,
) -> HookSubscription:
    subscription = await _service(request).update_configuration(
        actor=actor,
        subscription_id=subscription_id,
        if_match=if_match,
        request=body,
    )
    _set_etag(response, subscription)
    return subscription


@router.patch("/hook-subscriptions/{subscription_id}", response_model=HookSubscription)
async def update_hook_subscription_state(
    request: Request,
    response: Response,
    actor: Actor,
    subscription_id: str,
    body: UpdateHookSubscriptionStateRequest,
    if_match: IfMatch,
) -> HookSubscription:
    subscription = await _service(request).update_state(
        actor=actor,
        subscription_id=subscription_id,
        if_match=if_match,
        request=body,
    )
    _set_etag(response, subscription)
    return subscription


@router.delete("/hook-subscriptions/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_hook_subscription(
    request: Request,
    actor: Actor,
    subscription_id: str,
    if_match: IfMatch,
) -> None:
    await _service(request).delete(
        actor=actor,
        subscription_id=subscription_id,
        if_match=if_match,
    )


@router.post(
    "/hook-subscriptions/{subscription_id}/deliveries/{delivery_id}/redrive",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def redrive_hook_delivery(
    request: Request,
    actor: Actor,
    subscription_id: str,
    delivery_id: str,
) -> None:
    await _service(request).redrive(
        actor=actor,
        subscription_id=subscription_id,
        delivery_id=delivery_id,
    )


__all__ = ["router"]
