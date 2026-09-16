"""Relational reads backing authorized lifecycle reconciliation."""

from __future__ import annotations

from sqlalchemy import and_, false, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from a13n_service.interactions.models import RunAttemptRecord, RunRecord, SessionRecord

from .domain import LifecycleEntityType
from .models import LifecycleEventRecord
from .persistence import LifecycleReplayGap, LifecycleWorkspacePage


async def read_workspace_events(
    database: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    visible_agent_ids: frozenset[str] | None,
    after_seq: int | None,
    limit: int,
    configuration_access: ColumnElement[bool] | None = None,
) -> LifecycleWorkspacePage:
    session_join = and_(
        SessionRecord.organization_id == LifecycleEventRecord.organization_id,
        SessionRecord.id == LifecycleEventRecord.session_id,
    )
    boundary = select(func.min(LifecycleEventRecord.seq), func.max(LifecycleEventRecord.seq)).where(
        LifecycleEventRecord.organization_id == organization_id
    )
    events = select(LifecycleEventRecord).join(SessionRecord, session_join)
    filters = (
        LifecycleEventRecord.organization_id == organization_id,
        SessionRecord.workspace_id == workspace_id,
    )
    run_join = and_(
        RunRecord.organization_id == LifecycleEventRecord.organization_id,
        RunRecord.id == LifecycleEventRecord.run_id,
    )
    events = events.outerjoin(RunRecord, run_join).where(
        or_(
            and_(
                SessionRecord.configuration_owner_user_id.is_(None),
                true() if visible_agent_ids is None else RunRecord.agent_id.in_(visible_agent_ids),
            ),
            false() if configuration_access is None else configuration_access,
        )
    )
    floor, high = (await database.execute(boundary)).one()
    if floor is None or high is None:
        return LifecycleWorkspacePage((), None, 0, 0)
    if after_seq is not None and after_seq + 1 < floor:
        raise LifecycleReplayGap(retained_floor=floor, high_watermark=high)
    lower_bound = 0 if after_seq is None else after_seq
    records = (
        await database.scalars(
            events.where(*filters, LifecycleEventRecord.seq > lower_bound)
            .order_by(LifecycleEventRecord.seq)
            .limit(limit)
        )
    ).all()
    items = tuple(record.to_resource() for record in records)
    next_seq = items[-1].seq if items and items[-1].seq < high else None
    return LifecycleWorkspacePage(items, next_seq, floor, high)


async def load_owning_run(
    database: AsyncSession,
    *,
    workspace_id: str,
    resource_type: LifecycleEntityType,
    resource_id: str,
) -> RunRecord | None:
    query = (
        select(RunRecord)
        .join(
            SessionRecord,
            (SessionRecord.organization_id == RunRecord.organization_id) & (SessionRecord.id == RunRecord.session_id),
        )
        .where(SessionRecord.workspace_id == workspace_id)
    )
    if resource_type is LifecycleEntityType.run:
        return await database.scalar(query.where(RunRecord.id == resource_id))
    return await database.scalar(
        query.join(
            RunAttemptRecord,
            (RunAttemptRecord.organization_id == RunRecord.organization_id) & (RunAttemptRecord.run_id == RunRecord.id),
        ).where(RunAttemptRecord.id == resource_id)
    )


__all__ = ["load_owning_run", "read_workspace_events"]
