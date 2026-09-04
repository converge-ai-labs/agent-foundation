"""Workspace-scoped lifecycle queries over interaction-owned correlations."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.lifecycle import LifecycleEventRecord, LifecycleReplayGap, LifecycleWorkspacePage

from .models import SessionRecord


async def read_workspace_lifecycle_events(
    database: AsyncSession,
    *,
    tenant_id: str,
    workspace_id: str,
    after_seq: int | None,
    limit: int,
) -> LifecycleWorkspacePage:
    """Read one authorized Workspace projection over the tenant event cursor."""

    if after_seq is not None and after_seq < 1:
        raise ValueError("after_seq must be positive when supplied")
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    tenant_floor = await database.scalar(
        select(func.min(LifecycleEventRecord.seq)).where(LifecycleEventRecord.tenant_id == tenant_id)
    )
    workspace_boundary = await database.execute(
        select(
            func.min(LifecycleEventRecord.seq),
            func.max(LifecycleEventRecord.seq),
        )
        .join(
            SessionRecord,
            (SessionRecord.tenant_id == LifecycleEventRecord.tenant_id)
            & (SessionRecord.id == LifecycleEventRecord.session_id),
        )
        .where(
            LifecycleEventRecord.tenant_id == tenant_id,
            SessionRecord.workspace_id == workspace_id,
        )
    )
    workspace_floor, workspace_high = workspace_boundary.one()
    if workspace_floor is None or workspace_high is None or tenant_floor is None:
        return LifecycleWorkspacePage((), None, 0, 0)
    if after_seq is not None and after_seq < tenant_floor:
        raise LifecycleReplayGap(retained_floor=tenant_floor, high_watermark=workspace_high)
    filters = [
        LifecycleEventRecord.tenant_id == tenant_id,
        SessionRecord.workspace_id == workspace_id,
    ]
    if after_seq is not None:
        filters.append(LifecycleEventRecord.seq > after_seq)
    records = (
        await database.scalars(
            select(LifecycleEventRecord)
            .join(
                SessionRecord,
                (SessionRecord.tenant_id == LifecycleEventRecord.tenant_id)
                & (SessionRecord.id == LifecycleEventRecord.session_id),
            )
            .where(*filters)
            .order_by(LifecycleEventRecord.seq)
            .limit(limit)
        )
    ).all()
    items = tuple(record.to_resource() for record in records)
    return LifecycleWorkspacePage(
        items=items,
        next_seq=None if not items else items[-1].seq,
        retained_floor=tenant_floor,
        high_watermark=workspace_high,
    )


__all__ = ["read_workspace_lifecycle_events"]
