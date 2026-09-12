"""Inbox transitions owned by automatic asynchronous-result successors."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.interactions.control_domain import ThreadInboxKind, ThreadInboxStatus
from a13n_service.interactions.control_models import ThreadInboxRecord
from a13n_service.interactions.inbox_persistence import (
    ThreadInboxConflict,
    finalize_ineligible_async_results,
    lock_inbox_related_runs,
    release_pending_inbox_capacity,
)
from a13n_service.interactions.models import ThreadRecord


async def lock_unbound_async_entries(
    database: AsyncSession,
    *,
    thread: ThreadRecord,
    now: datetime,
) -> tuple[ThreadInboxRecord, ...]:
    """Lock, origin-gate, and return the inactive Thread's unbound async FIFO."""

    rows = tuple(
        (
            await database.scalars(
                select(ThreadInboxRecord)
                .where(
                    ThreadInboxRecord.organization_id == thread.organization_id,
                    ThreadInboxRecord.thread_id == thread.id,
                    ThreadInboxRecord.kind == ThreadInboxKind.async_subagent_result.value,
                    ThreadInboxRecord.status == ThreadInboxStatus.pending.value,
                    ThreadInboxRecord.target_run_id.is_(None),
                    ThreadInboxRecord.source_waiting_run_id.is_(None),
                )
                .order_by(ThreadInboxRecord.delivery_sequence, ThreadInboxRecord.id)
                .with_for_update()
            )
        ).all()
    )
    await finalize_ineligible_async_results(
        database,
        thread=thread,
        rows=rows,
        now=now,
    )
    return tuple(row for row in rows if row.status == ThreadInboxStatus.pending.value)


async def reconcile_pending_async_results(
    database: AsyncSession, *, organization_id: str, thread_id: str, now: datetime
) -> int:
    """Finalize expired or suppressed results regardless of their current binding."""
    thread = await database.scalar(
        select(ThreadRecord)
        .where(ThreadRecord.organization_id == organization_id, ThreadRecord.id == thread_id)
        .with_for_update()
    )
    if thread is None:
        return 0
    await lock_inbox_related_runs(database, organization_id=organization_id, thread_id=thread_id, required_run_ids=())
    rows = tuple(
        await database.scalars(
            select(ThreadInboxRecord)
            .where(
                ThreadInboxRecord.organization_id == organization_id,
                ThreadInboxRecord.thread_id == thread_id,
                ThreadInboxRecord.kind == ThreadInboxKind.async_subagent_result.value,
                ThreadInboxRecord.status == ThreadInboxStatus.pending.value,
            )
            .order_by(ThreadInboxRecord.delivery_sequence, ThreadInboxRecord.id)
            .with_for_update()
        )
    )
    await finalize_ineligible_async_results(database, thread=thread, rows=rows, now=now)
    return sum(row.status != ThreadInboxStatus.pending.value for row in rows)


def bind_locked_unbound_async_entries(
    rows: Sequence[ThreadInboxRecord],
    *,
    target_run_id: str | None = None,
    source_waiting_run_id: str | None = None,
) -> None:
    """Bind an already locked unbound async FIFO to one active or waiting owner."""

    if (target_run_id is None) == (source_waiting_run_id is None):
        raise ValueError("exactly one async result binding owner is required")
    for row in rows:
        if (
            row.status != ThreadInboxStatus.pending.value
            or row.kind != ThreadInboxKind.async_subagent_result.value
            or row.target_run_id is not None
            or row.source_waiting_run_id is not None
        ):
            raise ThreadInboxConflict("async result row is not a locked unbound pending entry")
        row.target_run_id = target_run_id
        row.source_waiting_run_id = source_waiting_run_id


def consume_async_result_for_successor(
    thread: ThreadRecord,
    row: ThreadInboxRecord,
    *,
    successor_run_id: str,
    state_digest_sha256: str,
    now: datetime,
) -> None:
    """Commit checkpoint-zero consumption for an automatic result successor."""

    if (
        row.status != ThreadInboxStatus.pending.value
        or row.kind != ThreadInboxKind.async_subagent_result.value
        or row.target_run_id is not None
        or row.source_waiting_run_id is not None
        or row.async_subagent_relationship_id is None
    ):
        raise ThreadInboxConflict("automatic successor requires one unbound async result")
    row.target_run_id = successor_run_id
    row.status = ThreadInboxStatus.consumed.value
    row.consumed_by_run_id = successor_run_id
    row.consumed_state_digest_sha256 = state_digest_sha256
    row.consumed_checkpoint_seq = 0
    row.finalized_at = now
    release_pending_inbox_capacity(thread, (row,))


__all__ = [
    "bind_locked_unbound_async_entries",
    "consume_async_result_for_successor",
    "lock_unbound_async_entries",
]
