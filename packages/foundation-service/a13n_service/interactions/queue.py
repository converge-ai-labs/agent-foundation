"""Atomic CRUD and ordering for editable queued Run submissions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from a13n_service.iam.domain import PrincipalRef
from a13n_service.storage import short_session, transaction

from .control_domain import (
    QueuedSubmission,
    QueuedSubmissionCollection,
    QueuedSubmissionMutationReceipt,
    QueuedSubmissionState,
    ThreadQueueMutationReceipt,
    ThreadRunSubmissionIntent,
    new_queued_submission_id,
)
from .control_models import QueuedSubmissionRecord
from .control_records import queued_submission_record
from .domain import Run, RunStatus, Thread
from .models import RunRecord, ThreadRecord


class ThreadSubmissionAdmission(StrEnum):
    waiting_continue = "waiting_continue"
    queued = "queued"
    continuation = "continuation"
    root = "root"


class QueuedSubmissionConflict(RuntimeError):
    """A queue resource or optimistic generation no longer matches."""


class QueuedSubmissionStore:
    """Own queue-only transitions; Run acceptance remains a separate boundary."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        max_queued: int = 256,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if max_queued < 1:
            raise ValueError("max_queued must be positive")
        self._sessions = sessions
        self._max_queued = max_queued
        self._clock = clock

    async def enqueue(
        self,
        *,
        tenant_id: str,
        thread_id: str,
        expected_thread_version: int,
        authority_principal: PrincipalRef,
        submission: ThreadRunSubmissionIntent,
        queued_submission_id: str | None = None,
    ) -> QueuedSubmissionMutationReceipt:
        now = _utc(self._clock())
        async with transaction(self._sessions) as database:
            thread = await _lock_thread(database, tenant_id=tenant_id, thread_id=thread_id)
            if thread.version != expected_thread_version:
                raise QueuedSubmissionConflict("Thread version changed before queue admission")
            rows = await _lock_live(database, tenant_id=tenant_id, thread_id=thread_id)
            current = await _load_run_snapshot(database, tenant_id=tenant_id, run_id=thread.current_run_id)
            head = (
                None
                if thread.head_run_id is None
                else await _load_run_snapshot(database, tenant_id=tenant_id, run_id=thread.head_run_id)
            )
            admission = classify_thread_submission(
                thread=thread.to_resource(),
                current=current.to_resource(),
                head=None if head is None else head.to_resource(),
                has_queued_submission=bool(rows),
                waiting_resolution_requested=False,
            )
            if admission is not ThreadSubmissionAdmission.queued:
                raise QueuedSubmissionConflict("Thread is eligible for immediate Run acceptance")
            if len(rows) >= self._max_queued:
                raise QueuedSubmissionConflict("Thread queued-submission capacity is exhausted")
            value = QueuedSubmission(
                queued_submission_id=queued_submission_id or new_queued_submission_id(),
                version=1,
                thread_id=thread_id,
                authority_principal=authority_principal,
                position=len(rows) + 1,
                submission=submission,
                submission_digest_sha256=submission.digest_sha256(),
                state=QueuedSubmissionState.queued,
                created_at=now,
                updated_at=now,
            )
            database.add(queued_submission_record(value, tenant_id=tenant_id))
            thread.queue_version += 1
            thread.updated_at = now
            await database.flush()
            return QueuedSubmissionMutationReceipt(
                queued_submission=value,
                queue_version=thread.queue_version,
            )

    async def list(
        self,
        *,
        tenant_id: str,
        thread_id: str,
        state: QueuedSubmissionState = QueuedSubmissionState.queued,
        limit: int = 100,
    ) -> QueuedSubmissionCollection:
        if limit < 1 or limit > 1000:
            raise ValueError("queue list limit must be between 1 and 1000")
        consumed = state is QueuedSubmissionState.consumed
        order = (
            (QueuedSubmissionRecord.consumed_at, QueuedSubmissionRecord.id)
            if consumed
            else (QueuedSubmissionRecord.position, QueuedSubmissionRecord.id)
        )
        async with short_session(self._sessions) as database:
            exists = await database.scalar(
                select(ThreadRecord.id).where(
                    ThreadRecord.tenant_id == tenant_id,
                    ThreadRecord.id == thread_id,
                )
            )
            if exists is None:
                raise QueuedSubmissionConflict("Thread was not found")
            rows = tuple(
                (
                    await database.scalars(
                        select(QueuedSubmissionRecord)
                        .where(
                            QueuedSubmissionRecord.tenant_id == tenant_id,
                            QueuedSubmissionRecord.thread_id == thread_id,
                            (
                                QueuedSubmissionRecord.consumed_run_id.is_not(None)
                                if consumed
                                else QueuedSubmissionRecord.consumed_run_id.is_(None)
                            ),
                        )
                        .order_by(*order)
                        .limit(limit)
                    )
                ).all()
            )
            return QueuedSubmissionCollection(items=tuple(row.to_resource() for row in rows))

    async def get(self, *, tenant_id: str, queued_submission_id: str) -> QueuedSubmission:
        async with short_session(self._sessions) as database:
            row = await _load(database, tenant_id=tenant_id, queued_submission_id=queued_submission_id)
            return row.to_resource()

    async def scan_drainable(self, *, tenant_id: str, limit: int = 100) -> tuple[str, ...]:
        """Find bounded terminal-Thread-plus-queue recovery authority."""

        if limit < 1 or limit > 1000:
            raise ValueError("queue drain scan limit must be between 1 and 1000")
        current = aliased(RunRecord)
        head = aliased(RunRecord)
        has_live_queue = exists(
            select(QueuedSubmissionRecord.id).where(
                QueuedSubmissionRecord.tenant_id == ThreadRecord.tenant_id,
                QueuedSubmissionRecord.thread_id == ThreadRecord.id,
                QueuedSubmissionRecord.consumed_run_id.is_(None),
            )
        )
        async with short_session(self._sessions) as database:
            return tuple(
                (
                    await database.scalars(
                        select(ThreadRecord.id)
                        .join(
                            current,
                            (current.tenant_id == ThreadRecord.tenant_id) & (current.id == ThreadRecord.current_run_id),
                        )
                        .outerjoin(
                            head,
                            (head.tenant_id == ThreadRecord.tenant_id) & (head.id == ThreadRecord.head_run_id),
                        )
                        .where(
                            ThreadRecord.tenant_id == tenant_id,
                            current.status.in_(("completed", "failed", "cancelled")),
                            or_(ThreadRecord.head_run_id.is_(None), head.status == "completed"),
                            has_live_queue,
                        )
                        .order_by(ThreadRecord.updated_at, ThreadRecord.id)
                        .limit(limit)
                    )
                ).all()
            )

    async def update(
        self,
        *,
        tenant_id: str,
        queued_submission_id: str,
        expected_version: int,
        actor_principal: PrincipalRef,
        submission: ThreadRunSubmissionIntent,
    ) -> QueuedSubmissionMutationReceipt:
        now = _utc(self._clock())
        async with transaction(self._sessions) as database:
            scope = await _scope(database, tenant_id=tenant_id, queued_submission_id=queued_submission_id)
            thread = await _lock_thread(database, tenant_id=tenant_id, thread_id=scope)
            row = await _lock_entry(database, tenant_id=tenant_id, queued_submission_id=queued_submission_id)
            if row.consumed_run_id is not None or row.version != expected_version:
                raise QueuedSubmissionConflict("queued submission version or lifecycle changed")
            resource = row.to_resource()
            if resource.authority_principal != actor_principal:
                raise QueuedSubmissionConflict("only the queued authority Principal can replace its intent")
            row.submission_json = submission.model_dump(mode="json", by_alias=True, exclude_none=True)
            row.submission_digest_sha256 = submission.digest_sha256()
            row.version += 1
            row.updated_at = now
            thread.queue_version += 1
            thread.updated_at = now
            await database.flush()
            return QueuedSubmissionMutationReceipt(
                queued_submission=row.to_resource(),
                queue_version=thread.queue_version,
            )

    async def delete(
        self,
        *,
        tenant_id: str,
        queued_submission_id: str,
        expected_version: int,
    ) -> ThreadQueueMutationReceipt:
        now = _utc(self._clock())
        async with transaction(self._sessions) as database:
            scope = await _scope(database, tenant_id=tenant_id, queued_submission_id=queued_submission_id)
            thread = await _lock_thread(database, tenant_id=tenant_id, thread_id=scope)
            rows = await _lock_live(database, tenant_id=tenant_id, thread_id=scope)
            target = next((row for row in rows if row.id == queued_submission_id), None)
            if target is None or target.version != expected_version:
                raise QueuedSubmissionConflict("queued submission version or lifecycle changed")
            deleted_position = target.position
            await database.delete(target)
            await database.flush()
            if deleted_position is None:
                raise QueuedSubmissionConflict("queued submission position is missing")
            shifted = tuple(
                row
                for row in rows
                if row is not target and row.position is not None and row.position > deleted_position
            )
            offset = len(rows)
            for row in shifted:
                position = row.position
                assert position is not None
                row.position = position + offset
            await database.flush()
            for row in shifted:
                position = row.position
                assert position is not None
                row.position = position - offset - 1
            thread.queue_version += 1
            thread.updated_at = now
            return ThreadQueueMutationReceipt(thread_id=thread.id, queue_version=thread.queue_version)

    async def reorder(
        self,
        *,
        tenant_id: str,
        thread_id: str,
        expected_queue_version: int,
        queued_submission_ids: tuple[str, ...],
    ) -> ThreadQueueMutationReceipt:
        now = _utc(self._clock())
        async with transaction(self._sessions) as database:
            thread = await _lock_thread(database, tenant_id=tenant_id, thread_id=thread_id)
            rows = await _lock_live(database, tenant_id=tenant_id, thread_id=thread_id)
            if thread.queue_version != expected_queue_version:
                raise QueuedSubmissionConflict("Thread queue version changed")
            if set(queued_submission_ids) != {row.id for row in rows} or len(queued_submission_ids) != len(rows):
                raise QueuedSubmissionConflict("reorder must name every queued submission exactly once")
            current = tuple(row.id for row in rows)
            if current == queued_submission_ids:
                return ThreadQueueMutationReceipt(thread_id=thread.id, queue_version=thread.queue_version)

            by_id = {row.id: row for row in rows}
            offset = len(rows) + max((row.position or 0 for row in rows), default=0)
            for index, entry_id in enumerate(queued_submission_ids, start=1):
                by_id[entry_id].position = offset + index
            await database.flush()
            for index, entry_id in enumerate(queued_submission_ids, start=1):
                by_id[entry_id].position = index
            thread.queue_version += 1
            thread.updated_at = now
            return ThreadQueueMutationReceipt(thread_id=thread.id, queue_version=thread.queue_version)


def classify_thread_submission(
    *,
    thread: Thread,
    current: Run,
    head: Run | None,
    has_queued_submission: bool,
    waiting_resolution_requested: bool,
) -> ThreadSubmissionAdmission:
    """Select the spec-defined queue-if-busy branch from one detached snapshot."""

    if current.id != thread.current_run_id or current.thread_id != thread.id:
        raise QueuedSubmissionConflict("current Run does not match the Thread snapshot")
    if head is None:
        if thread.head_run_id is not None:
            raise QueuedSubmissionConflict("Thread head Run is missing")
    elif head.id != thread.head_run_id or head.thread_id != thread.id:
        raise QueuedSubmissionConflict("head Run does not match the Thread snapshot")
    if waiting_resolution_requested:
        if current.status is not RunStatus.waiting or head is None or head.id != current.id:
            raise QueuedSubmissionConflict("waiting defaults require the selected current waiting head")
        return ThreadSubmissionAdmission.waiting_continue
    if has_queued_submission or current.status in {RunStatus.accepted, RunStatus.running, RunStatus.waiting}:
        return ThreadSubmissionAdmission.queued
    if head is not None and head.status is RunStatus.completed:
        return ThreadSubmissionAdmission.continuation
    if head is None and current.status in {RunStatus.failed, RunStatus.cancelled}:
        return ThreadSubmissionAdmission.root
    return ThreadSubmissionAdmission.queued


async def _scope(database: AsyncSession, *, tenant_id: str, queued_submission_id: str) -> str:
    thread_id = await database.scalar(
        select(QueuedSubmissionRecord.thread_id).where(
            QueuedSubmissionRecord.tenant_id == tenant_id,
            QueuedSubmissionRecord.id == queued_submission_id,
        )
    )
    if thread_id is None:
        raise QueuedSubmissionConflict("queued submission was not found")
    return thread_id


async def _lock_thread(database: AsyncSession, *, tenant_id: str, thread_id: str) -> ThreadRecord:
    thread = await database.scalar(
        select(ThreadRecord).where(ThreadRecord.tenant_id == tenant_id, ThreadRecord.id == thread_id).with_for_update()
    )
    if thread is None:
        raise QueuedSubmissionConflict("Thread was not found")
    return thread


async def _lock_live(
    database: AsyncSession,
    *,
    tenant_id: str,
    thread_id: str,
) -> tuple[QueuedSubmissionRecord, ...]:
    return tuple(
        (
            await database.scalars(
                select(QueuedSubmissionRecord)
                .where(
                    QueuedSubmissionRecord.tenant_id == tenant_id,
                    QueuedSubmissionRecord.thread_id == thread_id,
                    QueuedSubmissionRecord.consumed_run_id.is_(None),
                )
                .order_by(QueuedSubmissionRecord.position, QueuedSubmissionRecord.id)
                .with_for_update()
            )
        ).all()
    )


async def _load(
    database: AsyncSession,
    *,
    tenant_id: str,
    queued_submission_id: str,
) -> QueuedSubmissionRecord:
    row = await database.scalar(
        select(QueuedSubmissionRecord).where(
            QueuedSubmissionRecord.tenant_id == tenant_id,
            QueuedSubmissionRecord.id == queued_submission_id,
        )
    )
    if row is None:
        raise QueuedSubmissionConflict("queued submission was not found")
    return row


async def _load_run_snapshot(database: AsyncSession, *, tenant_id: str, run_id: str) -> RunRecord:
    run = await database.scalar(select(RunRecord).where(RunRecord.tenant_id == tenant_id, RunRecord.id == run_id))
    if run is None:
        raise QueuedSubmissionConflict("Thread-selected Run was not found")
    return run


async def _lock_entry(
    database: AsyncSession,
    *,
    tenant_id: str,
    queued_submission_id: str,
) -> QueuedSubmissionRecord:
    row = await database.scalar(
        select(QueuedSubmissionRecord)
        .where(
            QueuedSubmissionRecord.tenant_id == tenant_id,
            QueuedSubmissionRecord.id == queued_submission_id,
        )
        .with_for_update()
    )
    if row is None:
        raise QueuedSubmissionConflict("queued submission was not found")
    return row


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "QueuedSubmissionConflict",
    "QueuedSubmissionStore",
    "ThreadSubmissionAdmission",
    "classify_thread_submission",
]
