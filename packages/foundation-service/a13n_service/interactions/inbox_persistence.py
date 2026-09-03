"""Canonical relational transitions for the durable Thread inbox."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any, Literal

import rfc8785
from sqlalchemy import JSON, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .control_domain import ThreadInboxEntry, ThreadInboxKind, ThreadInboxStatus
from .control_models import ThreadInboxCounterRecord, ThreadInboxRecord
from .control_records import thread_inbox_record
from .models import RunRecord
from .objects import StoredRunState
from .state import ConsumedThreadInboxEntry


class ThreadInboxConflict(RuntimeError):
    """The selected inbox transition no longer matches relational authority."""


async def lock_inbox_related_runs(
    database: AsyncSession,
    *,
    tenant_id: str,
    thread_id: str,
    required_run_ids: Collection[str],
) -> tuple[RunRecord, ...]:
    """Lock required and pending-origin Runs together in stable ID order."""

    run_ids = set(required_run_ids)
    run_ids.update(
        value
        for value in (
            await database.scalars(
                select(ThreadInboxRecord.origin_run_id)
                .where(
                    ThreadInboxRecord.tenant_id == tenant_id,
                    ThreadInboxRecord.thread_id == thread_id,
                    ThreadInboxRecord.status == ThreadInboxStatus.pending.value,
                    ThreadInboxRecord.origin_run_id.is_not(None),
                )
                .distinct()
            )
        ).all()
        if value is not None
    )
    locked = tuple(
        (
            await database.scalars(
                select(RunRecord)
                .where(RunRecord.tenant_id == tenant_id, RunRecord.id.in_(sorted(run_ids)))
                .order_by(RunRecord.id)
                .with_for_update()
            )
        ).all()
    )
    if {run.id for run in locked} != run_ids:
        raise ThreadInboxConflict("Thread inbox Run authority was not found")
    return locked


async def allocate_steer(
    database: AsyncSession,
    *,
    tenant_id: str,
    thread_id: str,
    accepted_against_run_id: str,
    target_run_id: str | None,
    source_waiting_run_id: str | None,
    entry_id: str,
    payload: dict[str, Any],
    payload_size_bytes: int,
    max_pending_count: int,
    max_pending_bytes: int,
    now: datetime,
) -> ThreadInboxEntry:
    """Allocate one FIFO position and persist an accepted steer."""

    if payload_size_bytes < 1:
        raise ValueError("inbox payload size must be positive")
    if max_pending_count < 1 or max_pending_bytes < 1:
        raise ValueError("inbox admission limits must be positive")
    counter = await _lock_counter(database, tenant_id, thread_id)
    if counter.pending_count >= max_pending_count:
        raise ThreadInboxConflict("Thread inbox pending-entry capacity is exhausted")
    if counter.pending_bytes + payload_size_bytes > max_pending_bytes:
        raise ThreadInboxConflict("Thread inbox pending-byte capacity is exhausted")
    entry = ThreadInboxEntry(
        id=entry_id,
        tenant_id=tenant_id,
        thread_id=thread_id,
        kind=ThreadInboxKind.steer,
        delivery_sequence=counter.next_delivery_sequence,
        accepted_against_run_id=accepted_against_run_id,
        target_run_id=target_run_id,
        source_waiting_run_id=source_waiting_run_id,
        payload_schema_version="1",
        payload=payload,
        status=ThreadInboxStatus.pending,
        created_at=now,
    )
    database.add(thread_inbox_record(entry))
    counter.next_delivery_sequence += 1
    counter.pending_count += 1
    counter.pending_bytes += payload_size_bytes
    return entry


async def bind_waiting_entries(
    database: AsyncSession,
    *,
    tenant_id: str,
    thread_id: str,
    source_waiting_run_id: str,
    target_run_id: str,
    now: datetime,
) -> None:
    """Bind one waiting source's surviving FIFO to its direct successor."""

    rows = await _lock_pending_scope(
        database,
        tenant_id=tenant_id,
        thread_id=thread_id,
        source_waiting_run_id=source_waiting_run_id,
    )
    await _suppress_failed_origins(database, tenant_id=tenant_id, rows=rows, now=now)
    for row in rows:
        if row.status == ThreadInboxStatus.pending.value:
            row.target_run_id = target_run_id


async def bind_unbound_async_entries(
    database: AsyncSession,
    *,
    tenant_id: str,
    thread_id: str,
    target_run_id: str,
    now: datetime,
) -> None:
    """Bind eligible inactive-Thread async results after queue precedence wins."""

    origin_ids = tuple(
        value
        for value in (
            await database.scalars(
                select(ThreadInboxRecord.origin_run_id)
                .where(
                    ThreadInboxRecord.tenant_id == tenant_id,
                    ThreadInboxRecord.thread_id == thread_id,
                    ThreadInboxRecord.kind == ThreadInboxKind.async_subagent_result.value,
                    ThreadInboxRecord.status == ThreadInboxStatus.pending.value,
                    ThreadInboxRecord.target_run_id.is_(None),
                    ThreadInboxRecord.source_waiting_run_id.is_(None),
                )
                .distinct()
            )
        ).all()
        if value is not None
    )
    await _lock_runs(database, tenant_id=tenant_id, run_ids=origin_ids)
    await _lock_counter(database, tenant_id, thread_id)
    rows = tuple(
        (
            await database.scalars(
                select(ThreadInboxRecord)
                .where(
                    ThreadInboxRecord.tenant_id == tenant_id,
                    ThreadInboxRecord.thread_id == thread_id,
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
    await _suppress_failed_origins(database, tenant_id=tenant_id, rows=rows, now=now)
    for row in rows:
        if row.status == ThreadInboxStatus.pending.value:
            row.target_run_id = target_run_id


async def abandon_waiting_entries(
    database: AsyncSession,
    *,
    tenant_id: str,
    thread_id: str,
    source_waiting_run_id: str,
    now: datetime,
) -> None:
    """Finalize delivery retained by a waiting branch that is being abandoned."""

    rows = await _lock_pending_scope(
        database,
        tenant_id=tenant_id,
        thread_id=thread_id,
        source_waiting_run_id=source_waiting_run_id,
    )
    await _suppress_failed_origins(database, tenant_id=tenant_id, rows=rows, now=now)
    await _finalize_rows(database, rows, fallback=ThreadInboxStatus.superseded, now=now)


async def reconcile_checkpoint(
    database: AsyncSession,
    *,
    run: RunRecord,
    state: StoredRunState,
    now: datetime,
) -> None:
    """Commit every new, exact FIFO receipt selected by one durable checkpoint."""

    envelope = state.envelope
    if envelope.run_id != run.id or envelope.thread_id != run.thread_id:
        raise ThreadInboxConflict("checkpoint scope does not match the selected Run")
    receipts = envelope.host.consumed_inbox_entries
    if not receipts:
        return

    receipt_ids = tuple(receipt.inbox_entry_id for receipt in receipts)
    origin_ids = tuple(
        value
        for value in (
            await database.scalars(
                select(ThreadInboxRecord.origin_run_id)
                .where(
                    ThreadInboxRecord.tenant_id == run.tenant_id,
                    ThreadInboxRecord.thread_id == run.thread_id,
                    ThreadInboxRecord.target_run_id == run.id,
                    ThreadInboxRecord.status == ThreadInboxStatus.pending.value,
                    ThreadInboxRecord.origin_run_id.is_not(None),
                )
                .distinct()
            )
        ).all()
        if value is not None
    )
    await _lock_runs(database, tenant_id=run.tenant_id, run_ids=origin_ids)
    counter = await _lock_counter(database, run.tenant_id, run.thread_id)
    rows = tuple(
        (
            await database.scalars(
                select(ThreadInboxRecord)
                .where(
                    ThreadInboxRecord.tenant_id == run.tenant_id,
                    ThreadInboxRecord.thread_id == run.thread_id,
                    or_(
                        ThreadInboxRecord.id.in_(receipt_ids),
                        (
                            (ThreadInboxRecord.target_run_id == run.id)
                            & (ThreadInboxRecord.status == ThreadInboxStatus.pending.value)
                        ),
                    ),
                )
                .order_by(ThreadInboxRecord.delivery_sequence, ThreadInboxRecord.id)
                .with_for_update()
            )
        ).all()
    )
    by_id = {row.id: row for row in rows}
    if not set(receipt_ids) <= set(by_id):
        raise ThreadInboxConflict("checkpoint names an unknown Thread inbox entry")
    _validate_receipt_kinds(receipts, by_id)

    eligible = tuple(
        row for row in rows if row.target_run_id == run.id and row.status == ThreadInboxStatus.pending.value
    )
    await _suppress_failed_origins(database, tenant_id=run.tenant_id, rows=eligible, now=now)
    pending_receipt_ids = tuple(
        receipt.inbox_entry_id
        for receipt in receipts
        if by_id[receipt.inbox_entry_id].status == ThreadInboxStatus.pending.value
    )
    if not pending_receipt_ids:
        return
    consumable = tuple(row for row in eligible if row.status == ThreadInboxStatus.pending.value)
    selected = tuple(row.id for row in consumable[: len(pending_receipt_ids)])
    if selected != pending_receipt_ids:
        raise ThreadInboxConflict("checkpoint receipts are not the current eligible FIFO prefix")

    consumed_rows = tuple(by_id[entry_id] for entry_id in pending_receipt_ids)
    for row in consumed_rows:
        if row.target_run_id != run.id:
            raise ThreadInboxConflict("checkpoint receipt is not bound to the selected Run")
        row.status = ThreadInboxStatus.consumed.value
        row.consumed_by_run_id = run.id
        row.consumed_state_digest_sha256 = state.digest_sha256
        row.consumed_checkpoint_seq = envelope.checkpoint_seq
        row.finalized_at = now
    _release_pending(counter, consumed_rows)


async def apply_run_outcome(
    database: AsyncSession,
    *,
    run: RunRecord,
    outcome: Literal["waiting", "completed", "failed", "cancelled"],
    now: datetime,
    state: StoredRunState | None = None,
) -> None:
    """Apply the inbox half of a Run seal under already-held Thread/Run locks."""

    if state is not None:
        await reconcile_checkpoint(database, run=run, state=state, now=now)
    rows = await _lock_pending_scope(
        database,
        tenant_id=run.tenant_id,
        thread_id=run.thread_id,
        target_run_id=run.id,
        origin_run_id=run.id if outcome in {"failed", "cancelled"} else None,
    )
    await _suppress_failed_origins(
        database,
        tenant_id=run.tenant_id,
        rows=rows,
        now=now,
        terminal_origin_run_id=run.id if outcome in {"failed", "cancelled"} else None,
    )
    remaining = tuple(row for row in rows if row.status == ThreadInboxStatus.pending.value)
    if outcome == "completed":
        if remaining:
            raise ThreadInboxConflict("completed Run still has eligible pending inbox delivery")
        return
    if outcome == "waiting":
        for row in remaining:
            row.target_run_id = None
            row.source_waiting_run_id = run.id
        return
    await _finalize_rows(database, remaining, fallback=ThreadInboxStatus.superseded, now=now)


async def _lock_pending_scope(
    database: AsyncSession,
    *,
    tenant_id: str,
    thread_id: str,
    target_run_id: str | None = None,
    source_waiting_run_id: str | None = None,
    origin_run_id: str | None = None,
) -> tuple[ThreadInboxRecord, ...]:
    predicates = []
    if target_run_id is not None:
        predicates.append(ThreadInboxRecord.target_run_id == target_run_id)
    if source_waiting_run_id is not None:
        predicates.append(ThreadInboxRecord.source_waiting_run_id == source_waiting_run_id)
    if origin_run_id is not None:
        predicates.append(ThreadInboxRecord.origin_run_id == origin_run_id)
    if not predicates:
        raise ValueError("an inbox scope is required")

    origin_ids = tuple(
        value
        for value in (
            await database.scalars(
                select(ThreadInboxRecord.origin_run_id)
                .where(
                    ThreadInboxRecord.tenant_id == tenant_id,
                    ThreadInboxRecord.thread_id == thread_id,
                    ThreadInboxRecord.status == ThreadInboxStatus.pending.value,
                    or_(*predicates),
                    ThreadInboxRecord.origin_run_id.is_not(None),
                )
                .distinct()
            )
        ).all()
        if value is not None
    )
    await _lock_runs(database, tenant_id=tenant_id, run_ids=origin_ids)
    await _lock_counter(database, tenant_id, thread_id)
    return tuple(
        (
            await database.scalars(
                select(ThreadInboxRecord)
                .where(
                    ThreadInboxRecord.tenant_id == tenant_id,
                    ThreadInboxRecord.thread_id == thread_id,
                    ThreadInboxRecord.status == ThreadInboxStatus.pending.value,
                    or_(*predicates),
                )
                .order_by(ThreadInboxRecord.delivery_sequence, ThreadInboxRecord.id)
                .with_for_update()
            )
        ).all()
    )


async def _lock_counter(database: AsyncSession, tenant_id: str, thread_id: str) -> ThreadInboxCounterRecord:
    counter = await database.scalar(
        select(ThreadInboxCounterRecord)
        .where(
            ThreadInboxCounterRecord.tenant_id == tenant_id,
            ThreadInboxCounterRecord.thread_id == thread_id,
        )
        .with_for_update()
    )
    if counter is None:
        raise ThreadInboxConflict("Thread inbox counter was not found")
    return counter


async def _lock_runs(
    database: AsyncSession,
    *,
    tenant_id: str,
    run_ids: Collection[str],
) -> None:
    if not run_ids:
        return
    locked = tuple(
        (
            await database.scalars(
                select(RunRecord)
                .where(RunRecord.tenant_id == tenant_id, RunRecord.id.in_(sorted(run_ids)))
                .order_by(RunRecord.id)
                .with_for_update()
            )
        ).all()
    )
    if {run.id for run in locked} != set(run_ids):
        raise ThreadInboxConflict("Thread inbox origin Run was not found")


async def _suppress_failed_origins(
    database: AsyncSession,
    *,
    tenant_id: str,
    rows: Sequence[ThreadInboxRecord],
    now: datetime,
    terminal_origin_run_id: str | None = None,
) -> None:
    origin_ids = {
        row.origin_run_id
        for row in rows
        if row.kind == ThreadInboxKind.async_subagent_result.value and row.origin_run_id is not None
    }
    failed = set()
    if origin_ids:
        failed.update(
            (
                await database.scalars(
                    select(RunRecord.id).where(
                        RunRecord.tenant_id == tenant_id,
                        RunRecord.id.in_(origin_ids),
                        RunRecord.status.in_(("failed", "cancelled")),
                    )
                )
            ).all()
        )
    if terminal_origin_run_id is not None:
        failed.add(terminal_origin_run_id)
    suppressed = tuple(
        row for row in rows if row.status == ThreadInboxStatus.pending.value and row.origin_run_id in failed
    )
    if not suppressed:
        return
    counter = await _lock_counter(database, rows[0].tenant_id, rows[0].thread_id)
    for row in suppressed:
        _set_terminal(row, ThreadInboxStatus.suppressed, now)
    _release_pending(counter, suppressed)


async def _finalize_rows(
    database: AsyncSession,
    rows: Sequence[ThreadInboxRecord],
    *,
    fallback: ThreadInboxStatus,
    now: datetime,
) -> None:
    pending = tuple(row for row in rows if row.status == ThreadInboxStatus.pending.value)
    if not pending:
        return
    counter = await _lock_counter(database, pending[0].tenant_id, pending[0].thread_id)
    for row in pending:
        _set_terminal(row, fallback, now)
    _release_pending(counter, pending)


def _set_terminal(row: ThreadInboxRecord, status: ThreadInboxStatus, now: datetime) -> None:
    row.status = status.value
    row.target_run_id = None
    row.consumed_by_run_id = None
    row.consumed_state_digest_sha256 = None
    row.consumed_checkpoint_seq = None
    row.finalized_at = now


def _release_pending(counter: ThreadInboxCounterRecord, rows: Collection[ThreadInboxRecord]) -> None:
    counter.pending_count -= len(rows)
    counter.pending_bytes -= sum(_payload_size(row) for row in rows)
    if counter.pending_count < 0 or counter.pending_bytes < 0:
        raise ThreadInboxConflict("Thread inbox pending counters would become negative")


def _payload_size(row: ThreadInboxRecord) -> int:
    if row.payload_object_key is not None:
        if row.payload_object_size_bytes is None:
            raise ThreadInboxConflict("inbox object payload size is missing")
        return row.payload_object_size_bytes
    value = None if row.payload_json is JSON.NULL else row.payload_json
    try:
        return len(rfc8785.dumps(value))
    except rfc8785.CanonicalizationError as error:
        raise ThreadInboxConflict("inbox payload is not canonicalizable") from error


def _validate_receipt_kinds(
    receipts: Sequence[ConsumedThreadInboxEntry],
    rows: dict[str, ThreadInboxRecord],
) -> None:
    for receipt in receipts:
        if receipt.kind != rows[receipt.inbox_entry_id].kind:
            raise ThreadInboxConflict("checkpoint receipt kind does not match the inbox entry")


__all__ = [
    "ThreadInboxConflict",
    "abandon_waiting_entries",
    "allocate_steer",
    "apply_run_outcome",
    "bind_unbound_async_entries",
    "bind_waiting_entries",
    "lock_inbox_related_runs",
    "reconcile_checkpoint",
]
