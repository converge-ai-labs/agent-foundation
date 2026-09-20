"""Transactional HookSubscription creation, validation, and lifecycle matching."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.iam.models import WorkspaceRecord
from a13n_service.ids import new_object_id
from a13n_service.interactions.models import SessionRecord
from a13n_service.lifecycle.models import LifecycleEventRecord

from .domain import CreateHookSubscriptionRequest, InlineHookSubscriptionInput
from .invariants import (
    MAX_ACTIVE_HOOK_SUBSCRIPTIONS,
    HookSubscriptionInvariantCode,
    HookSubscriptionInvariantError,
    require_active_workspace_secret,
)
from .models import HookSubscriptionRecord, HookSubscriptionRevisionRecord


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

    return await create_hook_subscription(
        database,
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_type=actor_type,
        actor_id=actor_id,
        subscription=subscription.bind_run_scope(
            session_id=session_id,
            thread_id=thread_id,
            run_id=run_id,
        ),
        now=now,
        inline_run_id=run_id,
    )


async def create_hook_subscription(
    database: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    actor_type: str,
    actor_id: str,
    subscription: CreateHookSubscriptionRequest,
    now: datetime,
    inline_run_id: str | None = None,
) -> HookSubscriptionRecord:
    """Create one managed or exact-Run Hook head and immutable Revision v1."""

    await lock_hook_workspace(
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
    if inline_run_id is not None:
        existing = await database.scalar(
            select(HookSubscriptionRecord).where(
                HookSubscriptionRecord.organization_id == organization_id,
                HookSubscriptionRecord.inline_run_id == inline_run_id,
            )
        )
        if existing is not None:
            revision = await database.get(HookSubscriptionRevisionRecord, existing.current_revision_id)
            if revision is None or revision.configuration() != subscription:
                raise HookSubscriptionInvariantError(
                    HookSubscriptionInvariantCode.inline_conflict,
                    "inline Run already names a different Hook subscription",
                )
            return existing
    await require_hook_capacity(
        database,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )

    subscription_id = new_object_id("hsub")
    revision_id = new_object_id("hsubr")
    record = HookSubscriptionRecord(
        id=subscription_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        version=1,
        current_revision_id=revision_id,
        enabled=True,
        inline_run_id=inline_run_id,
        expired_at=None,
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
        session_id=subscription.session_id,
        thread_id=subscription.thread_id,
        run_id=subscription.run_id,
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


async def require_hook_capacity(
    database: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
) -> None:
    active_count = await database.scalar(
        select(func.count(HookSubscriptionRecord.id)).where(
            HookSubscriptionRecord.organization_id == organization_id,
            HookSubscriptionRecord.workspace_id == workspace_id,
            HookSubscriptionRecord.enabled.is_(True),
            HookSubscriptionRecord.expired_at.is_(None),
            HookSubscriptionRecord.deleted_at.is_(None),
        )
    )
    if active_count is None or active_count >= MAX_ACTIVE_HOOK_SUBSCRIPTIONS:
        raise HookSubscriptionInvariantError(
            HookSubscriptionInvariantCode.subscription_limit,
            "active Hook subscription limit exceeded",
        )


async def read_hook_subscriptions(
    database: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    limit: int,
    after: tuple[datetime, str] | None,
) -> tuple[tuple[HookSubscriptionRecord, HookSubscriptionRevisionRecord], ...]:
    query = (
        select(HookSubscriptionRecord, HookSubscriptionRevisionRecord)
        .join(
            HookSubscriptionRevisionRecord,
            HookSubscriptionRevisionRecord.id == HookSubscriptionRecord.current_revision_id,
        )
        .where(
            HookSubscriptionRecord.organization_id == organization_id,
            HookSubscriptionRecord.workspace_id == workspace_id,
            HookSubscriptionRecord.deleted_at.is_(None),
        )
    )
    if after is not None:
        updated_at, subscription_id = after
        query = query.where(
            or_(
                HookSubscriptionRecord.updated_at < updated_at,
                and_(
                    HookSubscriptionRecord.updated_at == updated_at,
                    HookSubscriptionRecord.id < subscription_id,
                ),
            )
        )
    rows = (
        await database.execute(
            query.order_by(
                HookSubscriptionRecord.updated_at.desc(),
                HookSubscriptionRecord.id.desc(),
            ).limit(limit)
        )
    ).all()
    return tuple((head, revision) for head, revision in rows)


async def write_hook_lifecycle(
    database: AsyncSession,
    event: LifecycleEventRecord,
) -> tuple[OutboxRecord, ...]:
    """Append matching delivery intents, then expire inline Hooks on sealed Runs."""

    # Observe subscription heads once. Only immutable Revisions are pinned until
    # their Outbox references commit; management does not lock those Revisions.
    statement = (
        select(HookSubscriptionRecord, HookSubscriptionRevisionRecord)
        .join(
            HookSubscriptionRevisionRecord,
            HookSubscriptionRevisionRecord.id == HookSubscriptionRecord.current_revision_id,
        )
        .join(
            SessionRecord,
            and_(
                SessionRecord.organization_id == HookSubscriptionRecord.organization_id,
                SessionRecord.workspace_id == HookSubscriptionRecord.workspace_id,
                SessionRecord.id == event.session_id,
            ),
        )
        .join(
            WorkspaceRecord,
            and_(
                WorkspaceRecord.organization_id == SessionRecord.organization_id,
                WorkspaceRecord.id == SessionRecord.workspace_id,
                WorkspaceRecord.deleted_at.is_(None),
            ),
        )
        .where(
            HookSubscriptionRecord.organization_id == event.organization_id,
            HookSubscriptionRecord.enabled.is_(True),
            HookSubscriptionRecord.expired_at.is_(None),
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
        .with_for_update(of=HookSubscriptionRevisionRecord, read=True, key_share=True)
    )
    statement = statement.where(HookSubscriptionRevisionRecord.hook_names.op("@>")(cast([event.event_type], JSONB)))
    candidates = (await database.execute(statement)).all()
    if len(candidates) > MAX_ACTIVE_HOOK_SUBSCRIPTIONS:
        raise HookSubscriptionInvariantError(
            HookSubscriptionInvariantCode.destination_limit,
            "active Hook destination limit exceeded",
        )

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
    )
    database.add_all(records)
    await database.flush()
    if event.event_type in {"run.waiting", "run.completed", "run.failed", "run.cancelled"}:
        await database.scalar(
            update(HookSubscriptionRecord)
            .where(
                HookSubscriptionRecord.organization_id == event.organization_id,
                HookSubscriptionRecord.inline_run_id == event.run_id,
                HookSubscriptionRecord.expired_at.is_(None),
            )
            .values(
                expired_at=event.occurred_at,
                updated_at=func.greatest(
                    event.occurred_at, HookSubscriptionRecord.updated_at + timedelta(microseconds=1)
                ),
            )
            .returning(HookSubscriptionRecord)
            .execution_options(populate_existing=True)
        )
    return records


async def load_inline_hook_subscription(
    database: AsyncSession,
    *,
    organization_id: str,
    run_id: str,
    lock: bool = False,
) -> tuple[HookSubscriptionRecord, HookSubscriptionRevisionRecord] | None:
    statement = (
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
    if lock:
        statement = statement.with_for_update(of=HookSubscriptionRecord)
    row = (await database.execute(statement)).one_or_none()
    return None if row is None else (row[0], row[1])


async def lock_hook_workspace(
    database: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
) -> WorkspaceRecord:
    # FOR NO KEY UPDATE serializes Hooks while allowing Session foreign-key checks.
    workspace = await database.scalar(
        select(WorkspaceRecord)
        .where(
            WorkspaceRecord.id == workspace_id,
            WorkspaceRecord.organization_id == organization_id,
            WorkspaceRecord.deleted_at.is_(None),
        )
        .with_for_update(of=WorkspaceRecord, key_share=True)
    )
    if workspace is None:
        raise HookSubscriptionInvariantError(
            HookSubscriptionInvariantCode.workspace_unavailable,
            "the Hook Workspace is unavailable",
        )
    return workspace


__all__ = [
    "MAX_ACTIVE_HOOK_SUBSCRIPTIONS",
    "HookSubscriptionInvariantCode",
    "HookSubscriptionInvariantError",
    "create_hook_subscription",
    "create_inline_hook_subscription",
    "load_inline_hook_subscription",
    "lock_hook_workspace",
    "read_hook_subscriptions",
    "require_active_workspace_secret",
    "require_hook_capacity",
    "write_hook_lifecycle",
]
