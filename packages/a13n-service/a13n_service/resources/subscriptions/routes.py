"""Webhook subscriptions, their delivery history and manual redelivery."""

from fastapi import APIRouter, Response

from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.resources.subscriptions import service
from a13n_service.resources.subscriptions.schemas import (
    CreatedSubscription,
    DeliveryPage,
    Subscription,
    SubscriptionCreate,
    SubscriptionPage,
    SubscriptionUpdate,
    WebhookDelivery,
)
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1/subscriptions", tags=["subscriptions"])


@router.post("", response_model=CreatedSubscription, status_code=201)
async def create_subscription(
    response: Response, workspace_id: WorkspaceId, body: SubscriptionCreate, actor: Actor, runtime: CurrentRuntime
) -> CreatedSubscription:
    """The response is the only time the signing secret is returned."""
    result = await service.create_subscription(
        runtime.storage,
        runtime.access,
        runtime.keys,
        runtime.endpoint_policy,
        actor,
        workspace_id,
        body,
        limit=runtime.settings.control.subscriptions,
    )
    return tagged(response, result)


@router.get("", response_model=SubscriptionPage)
async def list_subscriptions(
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> SubscriptionPage:
    return await service.list_subscriptions(
        runtime.storage, runtime.access, actor, workspace_id, limit=limit, cursor=cursor
    )


@router.get("/{subscription_id}", response_model=Subscription)
async def get_subscription(
    response: Response, workspace_id: WorkspaceId, subscription_id: str, actor: Actor, runtime: CurrentRuntime
) -> Subscription:
    result = await service.get_subscription(runtime.storage, runtime.access, actor, workspace_id, subscription_id)
    return tagged(response, result)


@router.patch("/{subscription_id}", response_model=Subscription)
async def update_subscription(
    response: Response,
    workspace_id: WorkspaceId,
    subscription_id: str,
    body: SubscriptionUpdate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Subscription:
    result = await service.update_subscription(
        runtime.storage,
        runtime.access,
        runtime.keys,
        runtime.endpoint_policy,
        actor,
        workspace_id,
        subscription_id,
        body,
        if_match=if_match,
    )
    return tagged(response, result)


@router.delete("/{subscription_id}", status_code=204)
async def delete_subscription(
    workspace_id: WorkspaceId, subscription_id: str, actor: Actor, runtime: CurrentRuntime, if_match: IfMatch = None
) -> Response:
    await service.delete_subscription(
        runtime.storage, runtime.access, actor, workspace_id, subscription_id, if_match=if_match
    )
    return Response(status_code=204)


@router.get("/{subscription_id}/deliveries", response_model=DeliveryPage)
async def list_deliveries(
    workspace_id: WorkspaceId,
    subscription_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> DeliveryPage:
    return await service.list_deliveries(
        runtime.storage, runtime.access, actor, workspace_id, subscription_id, limit=limit, cursor=cursor
    )


@router.post("/{subscription_id}/deliveries/{delivery_id}/redeliver", response_model=WebhookDelivery)
async def redeliver(
    workspace_id: WorkspaceId, subscription_id: str, delivery_id: str, actor: Actor, runtime: CurrentRuntime
) -> WebhookDelivery:
    return await service.redeliver(runtime.storage, runtime.access, actor, workspace_id, subscription_id, delivery_id)
