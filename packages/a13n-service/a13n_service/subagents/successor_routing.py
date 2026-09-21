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
    ThreadInboxRecord,
)
from a13n_service.interactions.domain import RunStatus
from a13n_service.interactions.inbox_persistence import lock_inbox_related_runs
from a13n_service.interactions.models import RunRecord, ThreadRecord

from .result_binding import select_result_binding
from .successor_inbox import bind_locked_unbound_async_entries, reconcile_async_result_inbox

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
    finalized_count: int = 0


@dataclass(slots=True)
class LockedAsyncResultSelection:
    thread: ThreadRecord
    entry: ThreadInboxRecord
    selected_parent: RunRecord
    origin: RunRecord
    later_entries: tuple[ThreadInboxRecord, ...]
    finalized_count: int


async def lock_and_route_async_result(
    database: AsyncSession,
    *,
    organization_id: str,
    thread_id: str,
    now: datetime,
) -> AsyncSubagentSuccessorReceipt | LockedAsyncResultSelection:
    """Apply origin gate and non-successor routes under Thread authority."""

    thread = await database.scalar(
        select(ThreadRecord)
        .where(ThreadRecord.organization_id == organization_id, ThreadRecord.id == thread_id)
        .with_for_update()
    )
    if thread is None:
        raise AsyncSubagentSuccessorError("parent Thread was not found")
    if thread.current_run_id is None:
        raise AsyncSubagentSuccessorError("Empty Thread has no child-result source")
    required_run_ids = {thread.current_run_id}
    if thread.head_run_id is not None:
        required_run_ids.add(thread.head_run_id)
    runs = {
        run.id: run
        for run in await lock_inbox_related_runs(
            database,
            organization_id=organization_id,
            thread_id=thread.id,
            required_run_ids=required_run_ids,
        )
    }
    current = runs[thread.current_run_id]
    head = None if thread.head_run_id is None else runs[thread.head_run_id]
    entries, finalized_count = await reconcile_async_result_inbox(database, thread=thread, now=now, locked_origins=runs)
    if not entries:
        return AsyncSubagentSuccessorReceipt(thread_id=thread_id, outcome="idle", finalized_count=finalized_count)
    first_entry_id = entries[0].id
    target_run_id, waiting_source_id = select_result_binding(
        thread.to_resource(), current.to_resource(), None if head is None else head.to_resource()
    )
    if target_run_id is not None:
        bind_locked_unbound_async_entries(entries, target_run_id=target_run_id)
        return AsyncSubagentSuccessorReceipt(
            thread_id=thread_id,
            outcome="bound_active",
            inbox_entry_id=first_entry_id,
            finalized_count=finalized_count,
        )
    if current.status == RunStatus.waiting.value and waiting_source_id is None:
        raise AsyncSubagentSuccessorError("waiting current Run is not the selected head")
    if waiting_source_id is not None:
        bind_locked_unbound_async_entries(entries, source_waiting_run_id=waiting_source_id)
        return AsyncSubagentSuccessorReceipt(
            thread_id=thread_id,
            outcome="retained_waiting",
            inbox_entry_id=first_entry_id,
            finalized_count=finalized_count,
        )
    if await _has_queued_submission(database, organization_id=organization_id, thread_id=thread_id):
        return AsyncSubagentSuccessorReceipt(
            thread_id=thread_id,
            outcome="queue_precedence",
            inbox_entry_id=first_entry_id,
            finalized_count=finalized_count,
        )
    selected_parent = _selected_completed_parent(thread, current, head)
    if selected_parent is None:
        raise AsyncSubagentSuccessorError("inactive Thread has no eligible selected state parent")
    origin_run_id = entries[0].origin_run_id
    if origin_run_id is None:
        raise AsyncSubagentSuccessorError("asynchronous result origin Run is missing")
    origin = runs[origin_run_id]
    if origin.status in {RunStatus.failed.value, RunStatus.cancelled.value}:
        raise AsyncSubagentSuccessorError("origin gate left an ineligible result pending")
    return LockedAsyncResultSelection(
        thread=thread,
        entry=entries[0],
        selected_parent=selected_parent,
        origin=origin,
        later_entries=entries[1:],
        finalized_count=finalized_count,
    )


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
    organization_id: str,
    thread_id: str,
) -> bool:
    return (
        await database.scalar(
            select(QueuedSubmissionRecord.id)
            .where(
                QueuedSubmissionRecord.organization_id == organization_id,
                QueuedSubmissionRecord.thread_id == thread_id,
                QueuedSubmissionRecord.position.is_not(None),
            )
            .order_by(QueuedSubmissionRecord.position, QueuedSubmissionRecord.id)
            .limit(1)
            .with_for_update()
        )
        is not None
    )


__all__ = [
    "AsyncSubagentSuccessorError",
    "AsyncSubagentSuccessorReceipt",
    "LockedAsyncResultSelection",
    "lock_and_route_async_result",
]
