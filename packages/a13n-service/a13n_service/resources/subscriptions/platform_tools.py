"""Admin-only nonsecret Webhook operations; private client forms own signing keys."""

from a13n_service.infra.http import PageLimit
from a13n_service.platform_tools import ToolContext as PlatformContext
from a13n_service.platform_tools import definition
from a13n_service.resources.runtime import Runtime
from a13n_service.resources.subscriptions import service
from a13n_service.resources.subscriptions.schemas import (
    DeliveryPage,
    Subscription,
    SubscriptionChanges,
    SubscriptionPage,
    SubscriptionUpdate,
    WebhookDelivery,
)

type ToolContext = PlatformContext[Runtime]


async def list_subscriptions(ctx: ToolContext, *, limit: PageLimit = 50, cursor: str | None = None) -> SubscriptionPage:
    """List Webhooks in this workspace; requires admin even for reads."""
    return await service.list_subscriptions(
        ctx.runtime.storage, ctx.runtime.access, ctx.actor, ctx.workspace_id, limit=limit, cursor=cursor
    )


async def get_subscription(ctx: ToolContext, *, subscription_id: str) -> Subscription:
    """Read a Webhook and its version, without its signing secret; requires admin."""
    return await service.get_subscription(
        ctx.runtime.storage, ctx.runtime.access, ctx.actor, ctx.workspace_id, subscription_id
    )


async def list_deliveries(
    ctx: ToolContext, *, subscription_id: str, limit: PageLimit = 50, cursor: str | None = None
) -> DeliveryPage:
    """Read newest deliveries and errors. Queued deliveries retain their original URL and key; requires admin."""
    return await service.list_deliveries(
        ctx.runtime.storage,
        ctx.runtime.access,
        ctx.actor,
        ctx.workspace_id,
        subscription_id,
        limit=limit,
        cursor=cursor,
    )


async def update_subscription(
    ctx: ToolContext, *, subscription_id: str, expected_version: str, body: SubscriptionChanges
) -> Subscription:
    """Change only supplied nonsecret fields, including enabled. Changes affect future deliveries, not queued ones; requires admin."""
    return await service.update_subscription(
        ctx.runtime.storage,
        ctx.runtime.access,
        ctx.runtime.keys,
        ctx.runtime.endpoint_policy,
        ctx.actor,
        ctx.workspace_id,
        subscription_id,
        SubscriptionUpdate.model_validate(body.model_dump(exclude_unset=True)),
        if_match=expected_version,
    )


async def delete_subscription(ctx: ToolContext, *, subscription_id: str, expected_version: str) -> None:
    """Delete a Webhook. Already queued deliveries still run with their original target; requires admin."""
    await service.delete_subscription(
        ctx.runtime.storage, ctx.runtime.access, ctx.actor, ctx.workspace_id, subscription_id, if_match=expected_version
    )


async def redeliver(ctx: ToolContext, *, subscription_id: str, delivery_id: str) -> WebhookDelivery:
    """Requeue a dead delivery with its original ID, URL and key. Success means queued, not delivered. Requires admin."""
    return await service.redeliver(
        ctx.runtime.storage, ctx.runtime.access, ctx.actor, ctx.workspace_id, subscription_id, delivery_id
    )


TOOLS = (
    definition(list_subscriptions, group="subscriptions", effect="read"),
    definition(get_subscription, group="subscriptions", effect="read"),
    definition(list_deliveries, group="subscriptions", effect="read"),
    definition(update_subscription, group="subscriptions", effect="write"),
    definition(delete_subscription, group="subscriptions", effect="write"),
    definition(redeliver, group="subscriptions", effect="write"),
)
