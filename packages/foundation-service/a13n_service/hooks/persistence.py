"""Transactional HookSubscription creation, validation, and lifecycle matching."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.iam.models import WorkspaceRecord
from a13n_service.ids import new_object_id
from a13n_service.interactions.models import SessionRecord
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.secrets.models import SecretRecord

from .domain import InlineHookSubscriptionInput
from .models import HookSubscriptionRecord, HookSubscriptionRevisionRecord

MAX_ACTIVE_HOOK_SUBSCRIPTIONS = 128


class HookSubscriptionInvariantError(RuntimeError):
    """A persisted Hook subscription invariant would make source commits unsafe."""


async def create_inline_hook_subscription(
    database: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    session_id: str,
    thread_id: str,
    run_id: str,
    actor_type: str,
    actor_id: str,
    subscription: InlineHookSubscriptionInput,
    now: datetime,
) -> HookSubscriptionRecord:
    """Create an exact-Run subscription before its accepted lifecycle fact."""

    await _lock_workspace(
        database,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )
    await require_active_workspace_secret(
        database,
        organization_id=organization_id,
        workspace_id=workspace_id,
        secret_id=subscription.webhook.signing_secret_id,
    )
    existing = await database.scalar(
        select(HookSubscriptionRecord).where(
            HookSubscriptionRecord.organization_id == organization_id,
            HookSubscriptionRecord.inline_run_id == run_id,
        )
    )
    if existing is not None:
        revision = await database.get(HookSubscriptionRevisionRecord, existing.current_revision_id)
        expected = subscription.bind_run_scope(session_id=session_id, thread_id=thread_id, run_id=run_id)
        if revision is None or revision.configuration() != expected:
            raise HookSubscriptionInvariantError("inline Run already names a different Hook subscription")
        return existing
    active_count = await database.scalar(
        select(func.count(HookSubscriptionRecord.id)).where(
            HookSubscriptionRecord.organization_id == organization_id,
            HookSubscriptionRecord.workspace_id == workspace_id,
            HookSubscriptionRecord.enabled.is_(True),
            HookSubscriptionRecord.deleted_at.is_(None),
        )
    )
    if active_count is None or active_count >= MAX_ACTIVE_HOOK_SUBSCRIPTIONS:
        raise HookSubscriptionInvariantError("active Hook subscription limit exceeded")

    subscription_id = new_object_id("hsub")
    revision_id = new_object_id("hsubr")
    record = HookSubscriptionRecord(
        id=subscription_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        version=1,
        current_revision_id=revision_id,
        enabled=True,
        inline_run_id=run_id,
        deleted_at=None,
        created_by_type=actor_type,
        created_by_id=actor_id,
        updated_by_type=actor_type,
        updated_by_id=actor_id,
        created_at=now,
        updated_at=now,
    )
    revision = HookSubscriptionRevisionRecord(
        id=revision_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        hook_subscription_id=subscription_id,
        version=1,
        hook_names=list(subscription.hook_names),
        session_id=session_id,
        thread_id=thread_id,
        run_id=run_id,
        endpoint_url=subscription.webhook.endpoint_url,
        signing_secret_id=subscription.webhook.signing_secret_id,
        signature_profile=subscription.webhook.signature_profile,
        created_by_type=actor_type,
        created_by_id=actor_id,
        created_at=now,
    )
    database.add_all((record, revision))
    await database.flush()
    return record


async def require_active_workspace_secret(
    database: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    secret_id: str,
) -> None:
    available = await database.scalar(
        select(SecretRecord.id).where(
            SecretRecord.id == secret_id,
            SecretRecord.organization_id == organization_id,
            SecretRecord.workspace_id == workspace_id,
            SecretRecord.owner_type == "workspace",
            SecretRecord.owner_id == workspace_id,
            SecretRecord.deleted_at.is_(None),
            SecretRecord.ciphertext.is_not(None),
        )
    )
    if available is None:
        raise HookSubscriptionInvariantError("the selected Hook signing Secret is unavailable")


async def append_matching_webhook_outbox(
    database: AsyncSession,
    event: LifecycleEventRecord,
) -> tuple[OutboxRecord, ...]:
    """Lock current matching heads and append one delivery intent per Revision."""

    workspace = await _lock_event_workspace(database, event)
    statement = (
        select(HookSubscriptionRecord, HookSubscriptionRevisionRecord)
        .join(
            HookSubscriptionRevisionRecord,
            HookSubscriptionRevisionRecord.id == HookSubscriptionRecord.current_revision_id,
        )
        .where(
            HookSubscriptionRecord.organization_id == event.tenant_id,
            HookSubscriptionRecord.workspace_id == workspace.id,
            HookSubscriptionRecord.enabled.is_(True),
            HookSubscriptionRecord.deleted_at.is_(None),
            or_(
                HookSubscriptionRevisionRecord.session_id.is_(None),
                HookSubscriptionRevisionRecord.session_id == event.session_id,
            ),
            or_(
                HookSubscriptionRevisionRecord.thread_id.is_(None),
                HookSubscriptionRevisionRecord.thread_id == event.thread_id,
            ),
            or_(
                HookSubscriptionRevisionRecord.run_id.is_(None),
                HookSubscriptionRevisionRecord.run_id == event.run_id,
            ),
        )
        .order_by(HookSubscriptionRecord.id)
        .limit(MAX_ACTIVE_HOOK_SUBSCRIPTIONS + 1)
        .with_for_update(of=HookSubscriptionRecord)
    )
    if database.get_bind().dialect.name == "postgresql":
        statement = statement.where(HookSubscriptionRevisionRecord.hook_names.op("@>")(cast([event.event_type], JSONB)))
    candidates = (await database.execute(statement)).all()
    if len(candidates) > MAX_ACTIVE_HOOK_SUBSCRIPTIONS:
        raise HookSubscriptionInvariantError("active Hook destination limit exceeded")

    now = event.created_at
    records = tuple(
        OutboxRecord(
            id=new_object_id("dlv"),
            source_kind="lifecycle_event",
            source_id=event.id,
            destination_kind="webhook",
            destination_ref=revision.id,
            status="pending",
            available_at=now,
            claim_generation=0,
            lease_expires_at=None,
            attempt_count=0,
            created_at=now,
            updated_at=now,
            published_at=None,
            dead_lettered_at=None,
            last_error_code=None,
        )
        for _, revision in candidates
        if event.event_type in revision.hook_names
    )
    database.add_all(records)
    await database.flush()
    return records


async def load_inline_hook_subscription(
    database: AsyncSession,
    *,
    organization_id: str,
    run_id: str,
) -> tuple[HookSubscriptionRecord, HookSubscriptionRevisionRecord] | None:
    row = (
        await database.execute(
            select(HookSubscriptionRecord, HookSubscriptionRevisionRecord)
            .join(
                HookSubscriptionRevisionRecord,
                HookSubscriptionRevisionRecord.id == HookSubscriptionRecord.current_revision_id,
            )
            .where(
                HookSubscriptionRecord.organization_id == organization_id,
                HookSubscriptionRecord.inline_run_id == run_id,
            )
        )
    ).one_or_none()
    return None if row is None else (row[0], row[1])


async def _lock_workspace(
    database: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
) -> WorkspaceRecord:
    workspace = await database.scalar(
        select(WorkspaceRecord)
        .where(
            WorkspaceRecord.id == workspace_id,
            WorkspaceRecord.organization_id == organization_id,
            WorkspaceRecord.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if workspace is None:
        raise HookSubscriptionInvariantError("the Hook Workspace is unavailable")
    return workspace


async def _lock_event_workspace(
    database: AsyncSession,
    event: LifecycleEventRecord,
) -> WorkspaceRecord:
    workspace = await database.scalar(
        select(WorkspaceRecord)
        .join(
            SessionRecord,
            (SessionRecord.workspace_id == WorkspaceRecord.id)
            & (SessionRecord.tenant_id == WorkspaceRecord.organization_id),
        )
        .where(
            SessionRecord.tenant_id == event.tenant_id,
            SessionRecord.id == event.session_id,
            WorkspaceRecord.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if workspace is None:
        raise HookSubscriptionInvariantError("lifecycle event has no active Hook Workspace")
    return workspace


__all__ = [
    "MAX_ACTIVE_HOOK_SUBSCRIPTIONS",
    "HookSubscriptionInvariantError",
    "append_matching_webhook_outbox",
    "create_inline_hook_subscription",
    "load_inline_hook_subscription",
    "require_active_workspace_secret",
]
