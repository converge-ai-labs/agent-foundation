"""Atomic lifecycle append and ordered read operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from a13n_harness import SafeFailure
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from .domain import LifecycleEntityType, LifecycleEvent, LifecycleEventDraft, LifecycleProjectionState
from .models import LifecycleEventRecord


@dataclass(frozen=True, slots=True)
class LifecycleResourcePage:
    items: tuple[LifecycleEvent, ...]
    next_resource_seq: int
    retained_resource_seq_floor: int
    high_watermark_resource_seq: int


@dataclass(frozen=True, slots=True)
class LifecycleWorkspacePage:
    items: tuple[LifecycleEvent, ...]
    next_seq: int | None
    retained_floor: int
    high_watermark: int


class LifecycleReplayGap(RuntimeError):
    def __init__(self, *, retained_floor: int, high_watermark: int) -> None:
        super().__init__("requested lifecycle history is no longer retained")
        self.retained_floor = retained_floor
        self.high_watermark = high_watermark


@dataclass(frozen=True, slots=True)
class LifecycleProjectionClaim:
    event: LifecycleEvent
    lease_owner: str


async def append_lifecycle_event(
    database: AsyncSession,
    draft: LifecycleEventDraft,
) -> LifecycleEventRecord:
    """Append a fact while the caller holds the owning resource mutation lock."""

    latest = await database.scalar(
        select(func.max(LifecycleEventRecord.resource_seq)).where(
            LifecycleEventRecord.tenant_id == draft.tenant_id,
            LifecycleEventRecord.entity_type == draft.entity_type.value,
            LifecycleEventRecord.entity_id == draft.entity_id,
        )
    )
    resource_seq = 1 if latest is None else latest + 1
    next_attempt = draft.occurred_at if draft.project_live else None
    projected_at = None if draft.project_live else draft.occurred_at
    record = LifecycleEventRecord(
        id=draft.id,
        tenant_id=draft.tenant_id,
        entity_type=draft.entity_type.value,
        entity_id=draft.entity_id,
        resource_seq=resource_seq,
        entity_version=draft.entity_version,
        event_type=draft.event_type,
        schema_version=draft.schema_version,
        mutation_id=draft.mutation_id,
        session_id=draft.session_id,
        thread_id=draft.thread_id,
        run_id=draft.run_id,
        run_attempt_id=draft.run_attempt_id,
        payload=draft.payload,
        actor_type=draft.actor_type,
        actor_id=draft.actor_id,
        occurred_at=draft.occurred_at,
        created_at=draft.occurred_at,
        projection_state=(
            LifecycleProjectionState.pending.value if draft.project_live else LifecycleProjectionState.projected.value
        ),
        projection_attempts=0,
        projection_next_attempt_at=next_attempt,
        projection_lease_owner=None,
        projection_lease_expires_at=None,
        projected_at=projected_at,
        projection_error_json=None,
    )
    database.add(record)
    await database.flush()
    return record


async def read_resource_events(
    database: AsyncSession,
    *,
    tenant_id: str,
    entity_type: LifecycleEntityType,
    entity_id: str,
    after_resource_seq: int,
    limit: int,
) -> LifecycleResourcePage:
    if after_resource_seq < 0:
        raise ValueError("after_resource_seq must be non-negative")
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    boundary = await database.execute(
        select(
            func.min(LifecycleEventRecord.resource_seq),
            func.max(LifecycleEventRecord.resource_seq),
        ).where(
            LifecycleEventRecord.tenant_id == tenant_id,
            LifecycleEventRecord.entity_type == entity_type.value,
            LifecycleEventRecord.entity_id == entity_id,
        )
    )
    floor, high = boundary.one()
    if floor is None or high is None:
        return LifecycleResourcePage((), 0, 0, 0)
    if after_resource_seq + 1 < floor:
        raise LifecycleReplayGap(retained_floor=floor, high_watermark=high)
    records = (
        await database.scalars(
            select(LifecycleEventRecord)
            .where(
                LifecycleEventRecord.tenant_id == tenant_id,
                LifecycleEventRecord.entity_type == entity_type.value,
                LifecycleEventRecord.entity_id == entity_id,
                LifecycleEventRecord.resource_seq > after_resource_seq,
            )
            .order_by(LifecycleEventRecord.resource_seq)
            .limit(limit)
        )
    ).all()
    items = tuple(record.to_resource() for record in records)
    next_resource_seq = after_resource_seq if not items else items[-1].resource_seq
    return LifecycleResourcePage(items, next_resource_seq, floor, high)


async def claim_lifecycle_projections(
    database: AsyncSession,
    *,
    lease_owner: str,
    now: datetime,
    lease_duration: timedelta,
    limit: int,
) -> tuple[LifecycleProjectionClaim, ...]:
    if not lease_owner or lease_duration <= timedelta(0):
        raise ValueError("projection lease owner and duration are required")
    if limit < 1 or limit > 200:
        raise ValueError("projection claim limit must be between 1 and 200")
    due = or_(
        and_(
            LifecycleEventRecord.projection_state.in_(
                (LifecycleProjectionState.pending.value, LifecycleProjectionState.retry_wait.value)
            ),
            LifecycleEventRecord.projection_next_attempt_at <= now,
        ),
        and_(
            LifecycleEventRecord.projection_state == LifecycleProjectionState.projecting.value,
            LifecycleEventRecord.projection_lease_expires_at <= now,
        ),
    )
    earlier = aliased(LifecycleEventRecord)
    no_earlier_unsettled_run_event = ~exists().where(
        earlier.tenant_id == LifecycleEventRecord.tenant_id,
        earlier.run_id == LifecycleEventRecord.run_id,
        earlier.seq < LifecycleEventRecord.seq,
        earlier.projection_state.in_(
            (
                LifecycleProjectionState.pending.value,
                LifecycleProjectionState.projecting.value,
                LifecycleProjectionState.retry_wait.value,
            )
        ),
    )
    records = (
        await database.scalars(
            select(LifecycleEventRecord)
            .where(due, no_earlier_unsettled_run_event)
            .order_by(LifecycleEventRecord.seq)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    claims: list[LifecycleProjectionClaim] = []
    for record in records:
        record.projection_state = LifecycleProjectionState.projecting.value
        record.projection_attempts += 1
        record.projection_next_attempt_at = None
        record.projection_lease_owner = lease_owner
        record.projection_lease_expires_at = now + lease_duration
        record.projected_at = None
        record.projection_error_json = None
        claims.append(LifecycleProjectionClaim(record.to_resource(), lease_owner))
    await database.flush()
    return tuple(claims)


async def complete_lifecycle_projection(
    database: AsyncSession,
    claim: LifecycleProjectionClaim,
    *,
    projected_at: datetime,
) -> bool:
    record = await _lock_projection_claim(database, claim, settled_at=projected_at)
    if record is None:
        return False
    record.projection_state = LifecycleProjectionState.projected.value
    record.projection_next_attempt_at = None
    record.projection_lease_owner = None
    record.projection_lease_expires_at = None
    record.projected_at = projected_at
    record.projection_error_json = None
    await database.flush()
    return True


async def fail_lifecycle_projection(
    database: AsyncSession,
    claim: LifecycleProjectionClaim,
    *,
    failed_at: datetime,
    retry_after: timedelta,
    max_attempts: int,
    failure: SafeFailure,
) -> bool:
    if retry_after < timedelta(0) or max_attempts < 1:
        raise ValueError("projection retry policy is invalid")
    record = await _lock_projection_claim(database, claim, settled_at=failed_at)
    if record is None:
        return False
    abandoned = record.projection_attempts >= max_attempts
    record.projection_state = (
        LifecycleProjectionState.abandoned.value if abandoned else LifecycleProjectionState.retry_wait.value
    )
    record.projection_next_attempt_at = None if abandoned else failed_at + retry_after
    record.projection_lease_owner = None
    record.projection_lease_expires_at = None
    record.projected_at = None
    record.projection_error_json = failure.model_dump(mode="json", by_alias=True)
    await database.flush()
    return True


async def _lock_projection_claim(
    database: AsyncSession,
    claim: LifecycleProjectionClaim,
    *,
    settled_at: datetime,
) -> LifecycleEventRecord | None:
    return await database.scalar(
        select(LifecycleEventRecord)
        .where(
            LifecycleEventRecord.seq == claim.event.seq,
            LifecycleEventRecord.projection_state == LifecycleProjectionState.projecting.value,
            LifecycleEventRecord.projection_lease_owner == claim.lease_owner,
            LifecycleEventRecord.projection_lease_expires_at > settled_at,
        )
        .with_for_update()
    )


__all__ = [
    "LifecycleProjectionClaim",
    "LifecycleReplayGap",
    "LifecycleResourcePage",
    "LifecycleWorkspacePage",
    "append_lifecycle_event",
    "claim_lifecycle_projections",
    "complete_lifecycle_projection",
    "fail_lifecycle_projection",
    "read_resource_events",
]
