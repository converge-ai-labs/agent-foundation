"""Locked relational routing for unbound asynchronous child results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.interactions.control_domain import RunAcceptanceReceipt
from a13n_service.interactions.control_models import (
    QueuedSubmissionRecord,
    ThreadInboxCounterRecord,
    ThreadInboxRecord,
)
from a13n_service.interactions.domain import RunStatus
from a13n_service.interactions.models import RunRecord, ThreadRecord

from .successor_inbox import bind_locked_unbound_async_entries, lock_unbound_async_entries

type SuccessorReconciliationOutcome = Literal[
    "idle",
    "bound_active",
    "retained_waiting",
    "queue_precedence",
    "run_accepted",
]


class AsyncSubagentSuccessorError(RuntimeError):
    """Inactive result routing found stale or incomplete durable authority."""


@dataclass(frozen=True, slots=True)
class AsyncSubagentSuccessorReceipt:
    thread_id: str
    outcome: SuccessorReconciliationOutcome
    inbox_entry_id: str | None = None
    successor: RunAcceptanceReceipt | None = None


@dataclass(slots=True)
class LockedAsyncResultSelection:
    thread: ThreadRecord
    entry: ThreadInboxRecord
    selected_parent: RunRecord
    origin: RunRecord
    counter: ThreadInboxCounterRecord
    later_entries: tuple[ThreadInboxRecord, ...]


async def lock_and_route_async_result(
    database: AsyncSession,
    *,
    tenant_id: str,
    thread_id: str,
    now: datetime,
) -> AsyncSubagentSuccessorReceipt | LockedAsyncResultSelection:
    """Apply origin gate and non-successor routes under Thread authority."""

    thread = await database.scalar(
        select(ThreadRecord).where(ThreadRecord.tenant_id == tenant_id, ThreadRecord.id == thread_id).with_for_update()
    )
    if thread is None:
        raise AsyncSubagentSuccessorError("parent Thread was not found")
    if thread.current_run_id is None:
        raise AsyncSubagentSuccessorError("Empty Thread has no child-result source")
    current = await _lock_run(database, tenant_id=tenant_id, run_id=thread.current_run_id)
    head = (
        None
        if thread.head_run_id is None
        else await _lock_run(database, tenant_id=tenant_id, run_id=thread.head_run_id)
    )
    required_run_ids = (current.id,) if head is None else (current.id, head.id)
    counter, entries = await lock_unbound_async_entries(
        database,
        tenant_id=tenant_id,
        thread_id=thread_id,
        required_run_ids=required_run_ids,
        now=now,
    )
    if not entries:
        return AsyncSubagentSuccessorReceipt(thread_id=thread_id, outcome="idle")
    first_entry_id = entries[0].id
    if current.status in {RunStatus.accepted.value, RunStatus.running.value}:
        bind_locked_unbound_async_entries(entries, target_run_id=current.id)
        return AsyncSubagentSuccessorReceipt(
            thread_id=thread_id,
            outcome="bound_active",
            inbox_entry_id=first_entry_id,
        )
    waiting_source = _selected_waiting_source(thread, current, head)
    if waiting_source is not None:
        bind_locked_unbound_async_entries(entries, source_waiting_run_id=waiting_source.id)
        return AsyncSubagentSuccessorReceipt(
            thread_id=thread_id,
            outcome="retained_waiting",
            inbox_entry_id=first_entry_id,
        )
    if await _has_queued_submission(database, tenant_id=tenant_id, thread_id=thread_id):
        return AsyncSubagentSuccessorReceipt(
            thread_id=thread_id,
            outcome="queue_precedence",
            inbox_entry_id=first_entry_id,
        )
    selected_parent = _selected_completed_parent(thread, current, head)
    if selected_parent is None:
        raise AsyncSubagentSuccessorError("inactive Thread has no eligible selected state parent")
    origin_run_id = entries[0].origin_run_id
    if origin_run_id is None:
        raise AsyncSubagentSuccessorError("asynchronous result origin Run is missing")
    origin = await _lock_run(database, tenant_id=tenant_id, run_id=origin_run_id)
    if origin.status in {RunStatus.failed.value, RunStatus.cancelled.value}:
        raise AsyncSubagentSuccessorError("origin gate left an ineligible result pending")
    return LockedAsyncResultSelection(
        thread=thread,
        entry=entries[0],
        selected_parent=selected_parent,
        origin=origin,
        counter=counter,
        later_entries=entries[1:],
    )


def _selected_waiting_source(
    thread: ThreadRecord,
    current: RunRecord,
    head: RunRecord | None,
) -> RunRecord | None:
    if current.status == RunStatus.waiting.value:
        if head is None or thread.head_run_id != current.id or head.id != current.id:
            raise AsyncSubagentSuccessorError("waiting current Run is not the selected head")
        return current
    if current.status in {RunStatus.failed.value, RunStatus.cancelled.value} and head is not None:
        if head.status == RunStatus.waiting.value:
            return head
    return None


def _selected_completed_parent(
    thread: ThreadRecord,
    current: RunRecord,
    head: RunRecord | None,
) -> RunRecord | None:
    if current.status == RunStatus.completed.value:
        if head is None or thread.head_run_id != current.id or head.id != current.id:
            raise AsyncSubagentSuccessorError("completed current Run is not the selected head")
        return current
    if current.status in {RunStatus.failed.value, RunStatus.cancelled.value} and head is not None:
        if head.status == RunStatus.completed.value:
            return head
    return None


async def _has_queued_submission(
    database: AsyncSession,
    *,
    tenant_id: str,
    thread_id: str,
) -> bool:
    rows = tuple(
        (
            await database.scalars(
                select(QueuedSubmissionRecord.id)
                .where(
                    QueuedSubmissionRecord.tenant_id == tenant_id,
                    QueuedSubmissionRecord.thread_id == thread_id,
                    QueuedSubmissionRecord.position.is_not(None),
                )
                .order_by(QueuedSubmissionRecord.position, QueuedSubmissionRecord.id)
                .with_for_update()
            )
        ).all()
    )
    return bool(rows)


async def _lock_run(database: AsyncSession, *, tenant_id: str, run_id: str) -> RunRecord:
    run = await database.scalar(
        select(RunRecord).where(RunRecord.tenant_id == tenant_id, RunRecord.id == run_id).with_for_update()
    )
    if run is None:
        raise AsyncSubagentSuccessorError("Thread-selected Run was not found")
    return run


__all__ = [
    "AsyncSubagentSuccessorError",
    "AsyncSubagentSuccessorReceipt",
    "LockedAsyncResultSelection",
    "lock_and_route_async_result",
]
