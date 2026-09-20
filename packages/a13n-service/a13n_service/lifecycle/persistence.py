"""Atomic lifecycle append and ordered read operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from a13n_harness import SafeFailure
from sqlalchemy import Table, and_, exists, func, insert, inspect, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.orm.attributes import set_committed_value

from a13n_service.interactions.models import RunAttemptRecord, RunRecord

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
    *,
    resource: RunRecord | RunAttemptRecord | None = None,
) -> LifecycleEventRecord:
    """Persist the locked resource mutation, its counter, and its fact together.

    The resource's pending scalar changes belong to this statement, not a later
    ORM flush. Callers retain their state/lease checks and owning mutation lock.
    """
    model = RunRecord if draft.entity_type is LifecycleEntityType.run else RunAttemptRecord
    table = cast(Table, model.__table__)
    changes: dict[str, Any] = {}
    attributes: dict[str, Any] = {}
    expected_version = None
    if resource is None:
        await database.flush()
    if resource is not None:
        if (
            not isinstance(resource, model)
            or resource.id != draft.entity_id
            or resource.organization_id != draft.organization_id
            or resource.version != draft.entity_version
        ):
            raise ValueError("Lifecycle fact does not match its resource mutation")
        state = inspect(resource)
        if state.session is not database.sync_session:
            raise ValueError("Lifecycle resource must belong to the current transaction")
        if not state.persistent:
            # Creation can have dependent inserts before lifecycle publication.
            await database.flush()
        if state.identity != (draft.entity_id,) or state.attrs.organization_id.history.has_changes():
            raise ValueError("Lifecycle mutation cannot change resource identity or organization")
        expected_version = resource.version
        version_history = state.attrs.version.history
        if version_history.deleted:
            expected_version = version_history.deleted[0]
        for attribute in state.mapper.column_attrs:
            if state.attrs[attribute.key].history.has_changes():
                value = getattr(resource, attribute.key)
                changes[attribute.columns[0].name] = value
                attributes[attribute.key] = value
    mutation = (
        update(table)
        .where(table.c.organization_id == draft.organization_id, table.c.id == draft.entity_id)
        .values(**(changes | {"lifecycle_seq": table.c.lifecycle_seq + 1}))
        .returning(table.c.lifecycle_seq)
    )
    if expected_version is not None:
        mutation = mutation.where(table.c.version == expected_version)
    changed = mutation.cte("lifecycle_mutation")
    next_attempt = draft.occurred_at if draft.project_live else None
    projected_at = None if draft.project_live else draft.occurred_at
    values = dict(
        id=draft.id,
        organization_id=draft.organization_id,
        entity_type=draft.entity_type.value,
        entity_id=draft.entity_id,
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
        hook_dispatch_state="pending",
        hook_dispatch_attempts=0,
        hook_dispatch_next_attempt_at=draft.occurred_at,
        hook_dispatched_at=None,
        hook_dispatch_error_json=None,
        projection_attempts=0,
        projection_next_attempt_at=next_attempt,
        projection_lease_owner=None,
        projection_lease_expires_at=None,
        projected_at=projected_at,
        projection_error_json=None,
    )
    event_table = LifecycleEventRecord.__table__
    statement = (
        insert(LifecycleEventRecord)
        .from_select(
            [*values, "resource_seq"],
            select(
                *(literal(value, type_=event_table.c[name].type) for name, value in values.items()),
                changed.c.lifecycle_seq,
            ),
        )
        .returning(LifecycleEventRecord)
    )
    record = (await database.scalars(statement)).one_or_none()
    if record is None:
        raise RuntimeError("Lifecycle resource is missing or its version changed")
    if resource is not None:
        for name, value in attributes.items():
            set_committed_value(resource, name, value)
        set_committed_value(resource, "lifecycle_seq", record.resource_seq)
    return record


async def read_resource_events(
    database: AsyncSession,
    *,
    organization_id: str,
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
            LifecycleEventRecord.organization_id == organization_id,
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
                LifecycleEventRecord.organization_id == organization_id,
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
        earlier.organization_id == LifecycleEventRecord.organization_id,
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


async def complete_publication_boundary(
    database: AsyncSession,
    event: LifecycleEvent,
    *,
    projected_at: datetime,
) -> bool:
    """Settle a confirmed Redis boundary shared by activation and projection repair."""
    if event.event_type not in {"run.accepted", "run_attempt.leased"}:
        raise ValueError("Only publication boundaries use activation settlement")
    record = await database.scalar(
        select(LifecycleEventRecord)
        .where(
            LifecycleEventRecord.organization_id == event.organization_id,
            LifecycleEventRecord.id == event.id,
            LifecycleEventRecord.event_type == event.event_type,
        )
        .with_for_update()
    )
    if record is None or record.projection_state == LifecycleProjectionState.abandoned.value:
        return False
    if record.projection_state == LifecycleProjectionState.projected.value:
        return True
    record.projection_state = LifecycleProjectionState.projected.value
    record.projection_next_attempt_at = None
    record.projection_lease_owner = None
    record.projection_lease_expires_at = None
    record.projected_at = projected_at
    record.projection_error_json = None
    await database.flush()
    return True


async def has_abandoned_run_projection(database: AsyncSession, organization_id: str, run_id: str) -> bool:
    """An abandoned fact prevents activation and complete retained presentation."""
    return bool(
        await database.scalar(
            select(
                exists().where(
                    LifecycleEventRecord.organization_id == organization_id,
                    LifecycleEventRecord.run_id == run_id,
                    LifecycleEventRecord.projection_state == LifecycleProjectionState.abandoned.value,
                )
            )
        )
    )


async def fail_lifecycle_projection(
    database: AsyncSession,
    claim: LifecycleProjectionClaim,
    *,
    failed_at: datetime,
    retry_after: timedelta,
    abandon: bool,
    failure: SafeFailure,
) -> bool:
    if retry_after < timedelta(0):
        raise ValueError("projection retry policy is invalid")
    record = await _lock_projection_claim(database, claim, settled_at=failed_at)
    if record is None:
        return False
    record.projection_state = (
        LifecycleProjectionState.abandoned.value if abandon else LifecycleProjectionState.retry_wait.value
    )
    record.projection_next_attempt_at = None if abandon else failed_at + retry_after
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
