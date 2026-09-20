"""Database-only claims, matching, and recovery for lifecycle Hook dispatch."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, cast, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.iam.models import WorkspaceRecord
from a13n_service.ids import new_object_id
from a13n_service.interactions.models import SessionRecord
from a13n_service.lifecycle.models import LifecycleEventRecord

from .invariants import MAX_ACTIVE_HOOK_SUBSCRIPTIONS, HookSubscriptionInvariantCode, HookSubscriptionInvariantError
from .models import HookSubscriptionRecord, HookSubscriptionRevisionRecord


async def claim_hook_events(database: AsyncSession, *, now: datetime, limit: int) -> tuple[LifecycleEventRecord, ...]:
    """Hold due event locks through matching and dispatch completion."""

    return tuple(
        await database.scalars(
            select(LifecycleEventRecord)
            .where(
                LifecycleEventRecord.hook_dispatch_state == "pending",
                LifecycleEventRecord.hook_dispatch_next_attempt_at <= now,
            )
            .order_by(LifecycleEventRecord.hook_dispatch_next_attempt_at, LifecycleEventRecord.seq)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )


async def dispatch_hook_event(database: AsyncSession, event: LifecycleEventRecord, *, now: datetime) -> int:
    """Match a locked pending event and atomically finish all of its fan-out."""

    if event.hook_dispatch_state != "pending":
        raise ValueError("Hook dispatch requires a locked pending event")
    # Observe subscription heads once. Only immutable Revisions are pinned until
    # their Outbox references commit; management does not lock those Revisions.
    statement = (
        select(HookSubscriptionRevisionRecord)
        .join(
            HookSubscriptionRecord,
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
            or_(
                HookSubscriptionRecord.expired_at.is_(None),
                HookSubscriptionRecord.inline_run_id == event.run_id,
            ),
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
        .limit(MAX_ACTIVE_HOOK_SUBSCRIPTIONS + 2)
        .with_for_update(of=HookSubscriptionRevisionRecord, read=True, key_share=True, nowait=True)
    )
    statement = statement.where(HookSubscriptionRevisionRecord.hook_names.op("@>")(cast([event.event_type], JSONB)))
    # Expiry releases capacity, so one expired inline owner can match alongside
    # the full active Workspace limit. Its exact Run scope prevents any more.
    candidates = (await database.scalars(statement)).all()
    if len(candidates) > MAX_ACTIVE_HOOK_SUBSCRIPTIONS + 1:
        raise HookSubscriptionInvariantError(
            HookSubscriptionInvariantCode.destination_limit,
            "active Hook destination limit exceeded",
        )

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
        for revision in candidates
    )
    database.add_all(records)
    event.hook_dispatch_state = "done"
    event.hook_dispatch_attempts += 1
    event.hook_dispatch_next_attempt_at = None
    event.hook_dispatched_at = now
    event.hook_dispatch_error_json = None
    await database.flush()
    return len(records)


async def retry_failed_hook_dispatch(
    database: AsyncSession, *, organization_id: str, event_id: str, now: datetime
) -> bool:
    """Explicitly give one failed event a fresh bounded dispatch attempt budget."""

    retried = await database.scalar(
        update(LifecycleEventRecord)
        .where(
            LifecycleEventRecord.organization_id == organization_id,
            LifecycleEventRecord.id == event_id,
            LifecycleEventRecord.hook_dispatch_state == "failed",
        )
        .values(
            hook_dispatch_state="pending",
            hook_dispatch_attempts=0,
            hook_dispatch_next_attempt_at=now,
            hook_dispatched_at=None,
            hook_dispatch_error_json=None,
        )
        .returning(LifecycleEventRecord.id)
    )
    return retried is not None
