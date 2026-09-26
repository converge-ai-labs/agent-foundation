"""Webhook subscriptions and inspection of their deliveries; all of it is workspace `admin` configuration."""

import secrets

from a13n_harness.providers.endpoint_policy import EndpointPolicy
from sqlalchemy import func, select

from a13n_service.infra import cursors
from a13n_service.infra.audit import record
from a13n_service.infra.crypto import KeyRing, SecretLocation
from a13n_service.infra.db import Storage, advisory_lock, assign, now
from a13n_service.infra.errors import conflict, invalid, not_found
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.outbox import OutboxRow
from a13n_service.resources.rows import audit_row, find_row, given, record_update
from a13n_service.resources.subscriptions.schemas import (
    SECRET_UNAVAILABLE,
    CreatedSubscription,
    DeliveryPage,
    Subscription,
    SubscriptionCreate,
    SubscriptionPage,
    SubscriptionUpdate,
    WebhookDelivery,
)
from a13n_service.resources.subscriptions.tables import SubscriptionRow
from a13n_service.tenancy.access import Access, administering_workspace
from a13n_service.tenancy.authorize import Principal


def signing_location(row: SubscriptionRow) -> SecretLocation:
    return SecretLocation(row.organization_id, "subscriptions", "signing_secret", row.id)


async def _permitted_url(
    storage: Storage,
    access: Access,
    policy: EndpointPolicy,
    actor: Principal,
    workspace_id: str,
    url: str,
    *,
    action: str,
) -> str:
    """Checked when configured and again, with fresh DNS answers, on every delivery attempt.

    Resolving a destination is outside any transaction, and only an administrator may make the service look
    one up, so authorization comes first; the change itself checks again in its own transaction.
    """
    async with administering_workspace(storage, access, actor, workspace_id, action=action, reading=True):
        pass
    try:
        return await policy.validate(url)
    except ValueError:
        raise invalid("url", "the destination is not permitted") from None


async def create_subscription(
    storage: Storage,
    access: Access,
    keys: KeyRing,
    policy: EndpointPolicy,
    actor: Principal,
    workspace_id: str,
    body: SubscriptionCreate,
    *,
    limit: int,
) -> CreatedSubscription:
    """The signing secret, generated unless supplied, is returned here and never again."""
    url = await _permitted_url(storage, access, policy, actor, workspace_id, body.url, action="subscription.create")
    secret = body.signing_secret.get_secret_value() if body.signing_secret else "whsec_" + secrets.token_urlsafe(32)
    async with administering_workspace(storage, access, actor, workspace_id, action="subscription.create") as (
        session,
        scope,
    ):
        # Serializes creates per workspace so concurrent requests cannot pass the count together.
        await advisory_lock(session, "subscriptions", scope.workspace_id)
        count = await session.scalar(
            select(func.count()).select_from(SubscriptionRow).where(SubscriptionRow.workspace_id == scope.workspace_id)
        )
        if (count or 0) >= limit:
            raise conflict("workspace", scope.workspace_id, "subscription_limit")
        row = SubscriptionRow(
            id=new_object_id("sub"),
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            name=body.name,
            url=url,
            kinds=body.kinds,
            filter=body.filter.model_dump(exclude_none=True),
            enabled=body.enabled,
            created_by_id=actor.id,
            updated_by_id=actor.id,
        )
        row.signing_secret = keys.protect(secret.encode(), signing_location(row)).model_dump()
        session.add(row)
        audit_row(session, actor, row, "create")
        await session.flush()
        return CreatedSubscription(**Subscription.model_validate(row).model_dump(), signing_secret=secret)


async def list_subscriptions(
    storage: Storage, access: Access, actor: Principal, workspace_id: str, *, limit: int, cursor: str | None
) -> SubscriptionPage:
    async with administering_workspace(
        storage, access, actor, workspace_id, action="subscription.read", reading=True
    ) as (session, scope):
        rows, next_cursor = await cursors.id_page(
            session,
            select(SubscriptionRow).where(SubscriptionRow.workspace_id == scope.workspace_id),
            SubscriptionRow.id,
            kind="subscriptions",
            owner=scope.workspace_id,
            cursor=cursor,
            limit=limit,
        )
        return SubscriptionPage(items=[Subscription.model_validate(row) for row in rows], next_cursor=next_cursor)


async def get_subscription(
    storage: Storage, access: Access, actor: Principal, workspace_id: str, subscription_id: str
) -> Subscription:
    async with administering_workspace(
        storage, access, actor, workspace_id, action="subscription.read", reading=True
    ) as (session, scope):
        return Subscription.model_validate(
            await find_row(session, actor, SubscriptionRow, scope, subscription_id, "read")
        )


async def update_subscription(
    storage: Storage,
    access: Access,
    keys: KeyRing,
    policy: EndpointPolicy,
    actor: Principal,
    workspace_id: str,
    subscription_id: str,
    body: SubscriptionUpdate,
    *,
    if_match: str | None,
) -> Subscription:
    """Changes select later transitions; deliveries already queued keep what they copied."""
    action = "subscription.update"
    url = (
        None
        if body.url is None
        else await _permitted_url(storage, access, policy, actor, workspace_id, body.url, action=action)
    )
    async with administering_workspace(storage, access, actor, workspace_id, action=action) as (session, scope):
        row = await find_row(session, actor, SubscriptionRow, scope, subscription_id, "read", lock=True)
        require_match(if_match, row.id, row.version)
        values = given(body, "name", "kinds", "enabled")
        if url is not None:
            values["url"] = url
        if body.filter is not None:
            values["filter"] = body.filter.model_dump(exclude_none=True)
        changed = assign(row, values)
        if body.signing_secret is not None:
            # Write-only: a replacement always changes the stored envelope, equal or not.
            row.signing_secret = keys.protect(
                body.signing_secret.get_secret_value().encode(), signing_location(row)
            ).model_dump()
            changed.append("signing_secret")
        if record_update(session, actor, row, changed):
            await session.flush()
        return Subscription.model_validate(row)


async def delete_subscription(
    storage: Storage, access: Access, actor: Principal, workspace_id: str, subscription_id: str, *, if_match: str | None
) -> None:
    """Queued deliveries remain and are still sent: they copied everything they need."""
    async with administering_workspace(storage, access, actor, workspace_id, action="subscription.delete") as (
        session,
        scope,
    ):
        row = await find_row(session, actor, SubscriptionRow, scope, subscription_id, "read", lock=True)
        require_match(if_match, row.id, row.version)
        audit_row(session, actor, row, "delete")
        await session.delete(row)


def _delivery(row: OutboxRow) -> WebhookDelivery:
    return WebhookDelivery.model_validate(
        {
            "id": row.id,
            "subscription_id": row.subscription_id,
            "url": row.target["url"],
            "status": row.status,
            "attempts": row.attempts,
            "available_at": row.available_at,
            "last_error": row.last_error,
            "created_at": row.created_at,
            "delivered_at": row.delivered_at,
            "payload": row.payload,
        }
    )


async def list_deliveries(
    storage: Storage,
    access: Access,
    actor: Principal,
    workspace_id: str,
    subscription_id: str,
    *,
    limit: int,
    cursor: str | None,
) -> DeliveryPage:
    """Newest first."""
    async with administering_workspace(
        storage, access, actor, workspace_id, action="subscription.read", reading=True
    ) as (session, scope):
        subscription = await find_row(session, actor, SubscriptionRow, scope, subscription_id, "read")
        rows, next_cursor = await cursors.keyset_page(
            session,
            select(OutboxRow).where(
                OutboxRow.kind == "webhook",
                OutboxRow.workspace_id == scope.workspace_id,
                OutboxRow.subscription_id == subscription.id,
            ),
            (OutboxRow.created_at, OutboxRow.id),
            kind="webhook_deliveries",
            owner=subscription.id,
            cursor=cursor,
            limit=limit,
            newest_first=True,
        )
        return DeliveryPage(items=[_delivery(row) for row in rows], next_cursor=next_cursor)


async def redeliver(
    storage: Storage, access: Access, actor: Principal, workspace_id: str, subscription_id: str, delivery_id: str
) -> WebhookDelivery:
    """Return a dead delivery to pending with a fresh attempt budget; the same delivery ID is sent again."""
    async with administering_workspace(storage, access, actor, workspace_id, action="webhook_delivery.redeliver") as (
        session,
        scope,
    ):
        subscription = await find_row(session, actor, SubscriptionRow, scope, subscription_id, "read")
        row = await session.scalar(
            select(OutboxRow)
            .where(
                OutboxRow.id == delivery_id,
                OutboxRow.kind == "webhook",
                OutboxRow.workspace_id == scope.workspace_id,
                OutboxRow.subscription_id == subscription.id,
            )
            .with_for_update()
        )
        if row is None:
            raise not_found("webhook_delivery", delivery_id)
        if row.status != "dead":
            raise conflict("webhook_delivery", row.id, "not_dead")
        # Staged without a signing secret, it can never be sent; a later transition copies a replaced secret.
        if "signing_secret" not in row.target:
            raise conflict("webhook_delivery", row.id, SECRET_UNAVAILABLE)
        row.status, row.attempts, row.available_at = "pending", 0, await now(session)
        row.settled_at = None
        record(
            session,
            scope,
            actor_id=actor.id,
            action="webhook_delivery.redeliver",
            target_kind="webhook_delivery",
            target_id=row.id,
        )
        await session.flush()
        return _delivery(row)
