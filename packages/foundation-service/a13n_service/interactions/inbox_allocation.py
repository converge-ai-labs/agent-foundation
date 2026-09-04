"""Admission and FIFO allocation for new durable Thread inbox entries."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from .control_domain import ThreadInboxEntry, ThreadInboxKind, ThreadInboxStatus
from .control_records import thread_inbox_record
from .domain import JsonObject
from .inbox_persistence import ThreadInboxCapacityExceeded, lock_inbox_counter


async def allocate_steer(
    database: AsyncSession,
    *,
    tenant_id: str,
    thread_id: str,
    accepted_against_run_id: str,
    target_run_id: str | None,
    source_waiting_run_id: str | None,
    entry_id: str,
    payload: JsonObject,
    payload_size_bytes: int,
    max_pending_count: int,
    max_pending_bytes: int,
    now: datetime,
) -> ThreadInboxEntry:
    """Allocate one FIFO position and persist an accepted steer."""

    _validate_admission(
        payload_size_bytes=payload_size_bytes,
        max_pending_count=max_pending_count,
        max_pending_bytes=max_pending_bytes,
    )
    counter = await lock_inbox_counter(database, tenant_id, thread_id)
    _require_capacity(
        pending_count=counter.pending_count,
        pending_bytes=counter.pending_bytes,
        payload_size_bytes=payload_size_bytes,
        max_pending_count=max_pending_count,
        max_pending_bytes=max_pending_bytes,
    )
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


async def allocate_async_result(
    database: AsyncSession,
    *,
    tenant_id: str,
    thread_id: str,
    origin_run_id: str,
    relationship_id: str,
    target_run_id: str | None,
    source_waiting_run_id: str | None,
    entry_id: str,
    payload: JsonObject,
    payload_size_bytes: int,
    suppressed: bool,
    max_pending_count: int,
    max_pending_bytes: int,
    now: datetime,
) -> ThreadInboxEntry:
    """Allocate one idempotent child-result FIFO position under locked authority."""

    _validate_admission(
        payload_size_bytes=payload_size_bytes,
        max_pending_count=max_pending_count,
        max_pending_bytes=max_pending_bytes,
    )
    counter = await lock_inbox_counter(database, tenant_id, thread_id)
    if not suppressed:
        _require_capacity(
            pending_count=counter.pending_count,
            pending_bytes=counter.pending_bytes,
            payload_size_bytes=payload_size_bytes,
            max_pending_count=max_pending_count,
            max_pending_bytes=max_pending_bytes,
        )
    entry = ThreadInboxEntry(
        id=entry_id,
        tenant_id=tenant_id,
        thread_id=thread_id,
        kind=ThreadInboxKind.async_subagent_result,
        delivery_sequence=counter.next_delivery_sequence,
        target_run_id=None if suppressed else target_run_id,
        source_waiting_run_id=None if suppressed else source_waiting_run_id,
        origin_run_id=origin_run_id,
        async_subagent_relationship_id=relationship_id,
        payload_schema_version="1",
        payload=payload,
        status=ThreadInboxStatus.suppressed if suppressed else ThreadInboxStatus.pending,
        created_at=now,
        finalized_at=now if suppressed else None,
    )
    database.add(thread_inbox_record(entry))
    counter.next_delivery_sequence += 1
    if not suppressed:
        counter.pending_count += 1
        counter.pending_bytes += payload_size_bytes
    return entry


def _validate_admission(
    *,
    payload_size_bytes: int,
    max_pending_count: int,
    max_pending_bytes: int,
) -> None:
    if payload_size_bytes < 1:
        raise ValueError("inbox payload size must be positive")
    if max_pending_count < 1 or max_pending_bytes < 1:
        raise ValueError("inbox admission limits must be positive")


def _require_capacity(
    *,
    pending_count: int,
    pending_bytes: int,
    payload_size_bytes: int,
    max_pending_count: int,
    max_pending_bytes: int,
) -> None:
    if pending_count >= max_pending_count:
        raise ThreadInboxCapacityExceeded("Thread inbox pending-entry capacity is exhausted")
    if pending_bytes + payload_size_bytes > max_pending_bytes:
        raise ThreadInboxCapacityExceeded("Thread inbox pending-byte capacity is exhausted")


__all__ = ["allocate_async_result", "allocate_steer"]
