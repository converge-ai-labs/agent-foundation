"""Control-owned owner-loss reconciliation and bounded result expiry."""

from datetime import timedelta

from sqlalchemy import delete, func, or_, select, update

from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.outbox import OutboxRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import RunRow, ToolExecutionRow
from a13n_service.runs.tool_executions.store import notify, observation


async def reconcile(runtime: Runtime, *, limit: int = 100) -> int:
    async with transaction(runtime.storage) as session:
        current = await session.scalar(select(func.clock_timestamp()))
        assert current is not None
        rows = (
            await session.scalars(
                select(ToolExecutionRow)
                .where(
                    ToolExecutionRow.completed_at.is_(None),
                    ToolExecutionRow.lease_expires_at <= current,
                )
                .order_by(ToolExecutionRow.lease_expires_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row in rows:
            record = observation(row).model_copy(
                update={
                    "status": "lost",
                    "completed_at": current,
                    "result_available": False,
                    "error": "The execution owner was lost. The external outcome may be unknown.",
                    "foreground_released": True,
                }
            )
            row.status, row.record, row.completed_at = "lost", record.model_dump(mode="json"), current
            row.expires_at = current + timedelta(seconds=runtime.settings.tool_executions.max_retention_seconds)
            await notify(session, row)
        return len(rows)


async def prune(runtime: Runtime, *, limit: int = 100) -> int:
    recoverable = (
        select(RunRow.id)
        .where(RunRow.id == ToolExecutionRow.run_id, RunRow.status.in_(["accepted", "running"]))
        .exists()
    )
    async with short_session(runtime.storage) as session:
        expired = list(
            await session.execute(
                select(ToolExecutionRow.id, ToolExecutionRow.workspace_id)
                .where(
                    ToolExecutionRow.expires_at <= func.clock_timestamp(),
                    or_(ToolExecutionRow.payload_pruned.is_(False), ~recoverable),
                )
                .order_by(ToolExecutionRow.expires_at)
                .limit(limit)
            )
        )
    for execution_id, workspace_id in expired:
        # Expiry is immutable and reads already reject it. Leave the row until every object deletion succeeds,
        # so a crash retries cleanup. Include unreferenced bytes from an interrupted publication.
        prefix = f"tool-executions/{workspace_id}/{execution_id}"
        keys = await runtime.objects.keys(prefix, limit=100)
        for key in keys:
            await runtime.objects.delete(key)
        if len(keys) == 100:
            continue
        async with transaction(runtime.storage) as session:
            await session.execute(
                delete(OutboxRow).where(OutboxRow.kind == "tool_result", OutboxRow.dedupe_key == execution_id)
            )
            # Keep only the admission identity while the origin can still recover. Exclude this tombstone
            # from later batches so active origins cannot starve cleanup of other expired results.
            await session.execute(
                update(ToolExecutionRow)
                .where(ToolExecutionRow.id == execution_id)
                .values(
                    payload_pruned=True,
                    result_ref=None,
                )
            )
            await session.execute(
                delete(ToolExecutionRow).where(
                    ToolExecutionRow.id == execution_id,
                    ToolExecutionRow.expires_at <= func.clock_timestamp(),
                    ~recoverable,
                )
            )
    return len(expired)
