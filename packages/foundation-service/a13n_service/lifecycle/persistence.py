"""Atomic lifecycle append and ordered read operations."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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


__all__ = [
    "LifecycleReplayGap",
    "LifecycleResourcePage",
    "LifecycleWorkspacePage",
    "append_lifecycle_event",
    "read_resource_events",
]
