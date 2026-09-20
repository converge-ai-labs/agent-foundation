"""Atomic CRUD and ordering for editable queued Run submissions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum

from sqlalchemy import delete, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.hooks import InlineHookValidationError, InlineHookValidator
from a13n_service.iam.domain import PrincipalRef
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

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
from .models import RunRecord, SessionRecord, ThreadRecord


class ThreadSubmissionAdmission(StrEnum):
    waiting_continue = "waiting_continue"
    queued = "queued"
    continuation = "continuation"
    root = "root"
    reject = "reject"


class QueuedSubmissionConflict(RuntimeError):
    """A queue resource or optimistic generation no longer matches."""


class QueuedSubmissionStore:
    """Own queue-only transitions; Run acceptance remains a separate boundary."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        inline_hooks: InlineHookValidator,
        *,
        max_queued: int = 256,
        clock: Clock = utc_now,
    ) -> None:
        if max_queued < 1:
            raise ValueError("max_queued must be positive")
        self._sessions = sessions
        self._inline_hooks = inline_hooks
        self._max_queued = max_queued
        self._clock = clock

    async def enqueue(
        self,
        *,
        organization_id: str,
        thread_id: str,
        expected_thread_version: int,
        authority_principal: PrincipalRef,
        submission: ThreadRunSubmissionIntent,
        queued_submission_id: str | None = None,
        request_key: str | None = None,
        replay: Callable[[AsyncSession], Awaitable[QueuedSubmissionMutationReceipt | None]] | None = None,
        transaction_hook: (Callable[[AsyncSession, QueuedSubmissionMutationReceipt], Awaitable[None]] | None) = None,
    ) -> QueuedSubmissionMutationReceipt:
        await self._validate_inline_hook_destination(submission)
        now = assume_utc(self._clock())
        async with transaction(self._sessions) as database:
            thread = await _lock_thread(database, organization_id=organization_id, thread_id=thread_id)
            if replay is not None and (replayed := await replay(database)) is not None:
                return replayed
            if thread.version != expected_thread_version:
                raise QueuedSubmissionConflict("Thread version changed before queue admission")
            rows = await _lock_live(database, organization_id=organization_id, thread_id=thread_id)
            current = (
                await _load_run_snapshot(database, organization_id=organization_id, run_id=thread.current_run_id)
                if thread.current_run_id
                else None
            )
            head = (
                None
                if thread.head_run_id is None
                else await _load_run_snapshot(database, organization_id=organization_id, run_id=thread.head_run_id)
            )
            admission = classify_thread_submission(
                thread=thread.to_resource(),
                current=current.to_resource() if current else None,
                head=None if head is None else head.to_resource(),
                has_queued_submission=bool(rows),
                waiting_resolution_requested=False,
            )
            if admission is ThreadSubmissionAdmission.reject:
                raise QueuedSubmissionConflict("failed or cancelled current Run cannot admit queued work")
            if admission is not ThreadSubmissionAdmission.queued:
                raise QueuedSubmissionConflict("Thread is eligible for immediate Run acceptance")
            if len(rows) >= self._max_queued:
                raise QueuedSubmissionConflict("Thread queued-submission capacity is exhausted")
            await self._authorize_inline_hook(
                database,
                organization_id=organization_id,
                workspace_id=await _load_workspace_id(database, thread),
                agent_id=submission.agent_id or (current.agent_id if current else ""),
                principal=authority_principal,
                submission=submission,
            )
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
            record = queued_submission_record(value, organization_id=organization_id)
            record.request_key = request_key
            database.add(record)
            thread.queue_version += 1
            thread.updated_at = now
            await database.flush()
            receipt = QueuedSubmissionMutationReceipt(
                queued_submission=value,
                queue_version=thread.queue_version,
            )
            if transaction_hook is not None:
                await transaction_hook(database, receipt)
            return receipt

    async def list(
        self,
        *,
        organization_id: str,
        thread_id: str,
        state: QueuedSubmissionState = QueuedSubmissionState.queued,
        limit: int = 100,
    ) -> QueuedSubmissionCollection:
        if limit < 1 or limit > 1000:
            raise ValueError("queue list limit must be between 1 and 1000")
        lifecycle_column = {
            QueuedSubmissionState.queued: QueuedSubmissionRecord.position,
            QueuedSubmissionState.consumed: QueuedSubmissionRecord.consumed_at,
            QueuedSubmissionState.failed: QueuedSubmissionRecord.failed_at,
        }[state]
        async with short_session(self._sessions) as database:
            exists = await database.scalar(
                select(ThreadRecord.id).where(
                    ThreadRecord.organization_id == organization_id,
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
                            QueuedSubmissionRecord.organization_id == organization_id,
                            QueuedSubmissionRecord.thread_id == thread_id,
                            lifecycle_column.is_not(None),
                        )
                        .order_by(lifecycle_column, QueuedSubmissionRecord.id)
                        .limit(limit)
                    )
                ).all()
            )
            return QueuedSubmissionCollection(items=tuple(row.to_resource() for row in rows))

    async def get(self, *, organization_id: str, queued_submission_id: str) -> QueuedSubmission:
        async with short_session(self._sessions) as database:
            row = await _load(database, organization_id=organization_id, queued_submission_id=queued_submission_id)
            return row.to_resource()

    async def scan_drainable(self, *, organization_id: str, limit: int = 100) -> tuple[str, ...]:
        """Find bounded terminal-Thread-plus-queue recovery authority."""

        if limit < 1 or limit > 1000:
            raise ValueError("queue drain scan limit must be between 1 and 1000")
        current = aliased(RunRecord)
        head = aliased(RunRecord)
        has_live_queue = exists(
            select(QueuedSubmissionRecord.id).where(
                QueuedSubmissionRecord.organization_id == ThreadRecord.organization_id,
                QueuedSubmissionRecord.thread_id == ThreadRecord.id,
                QueuedSubmissionRecord.position.is_not(None),
            )
        )
        async with short_session(self._sessions) as database:
            return tuple(
                (
                    await database.scalars(
                        select(ThreadRecord.id)
                        .join(
                            current,
                            (current.organization_id == ThreadRecord.organization_id)
                            & (current.id == ThreadRecord.current_run_id),
                        )
                        .outerjoin(
                            head,
                            (head.organization_id == ThreadRecord.organization_id)
                            & (head.id == ThreadRecord.head_run_id),
                        )
                        .where(
                            ThreadRecord.organization_id == organization_id,
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
        organization_id: str,
        queued_submission_id: str,
        expected_version: int,
        actor_principal: PrincipalRef,
        submission: ThreadRunSubmissionIntent,
        replay: Callable[[AsyncSession], Awaitable[QueuedSubmissionMutationReceipt | None]] | None = None,
        transaction_hook: (Callable[[AsyncSession, QueuedSubmissionMutationReceipt], Awaitable[None]] | None) = None,
    ) -> QueuedSubmissionMutationReceipt:
        await self._validate_inline_hook_destination(submission)
        now = assume_utc(self._clock())
        async with transaction(self._sessions) as database:
            if replay is not None and (replayed := await replay(database)) is not None:
                return replayed
            scope = await _scope(database, organization_id=organization_id, queued_submission_id=queued_submission_id)
            thread = await _lock_thread(database, organization_id=organization_id, thread_id=scope)
            row = await _lock_entry(
                database, organization_id=organization_id, queued_submission_id=queued_submission_id
            )
            if row.position is None or row.version != expected_version:
                raise QueuedSubmissionConflict("queued submission version or lifecycle changed")
            resource = row.to_resource()
            if resource.authority_principal != actor_principal:
                raise QueuedSubmissionConflict("only the queued authority Principal can replace its intent")
            current = (
                await _load_run_snapshot(database, organization_id=organization_id, run_id=thread.current_run_id)
                if thread.current_run_id
                else None
            )
            await self._authorize_inline_hook(
                database,
                organization_id=organization_id,
                workspace_id=await _load_workspace_id(database, thread),
                agent_id=submission.agent_id or (current.agent_id if current else ""),
                principal=actor_principal,
                submission=submission,
            )
            row.submission_json = submission.retained_payload()
            row.submission_digest_sha256 = submission.digest_sha256()
            row.version += 1
            row.updated_at = now
            thread.queue_version += 1
            thread.updated_at = now
            await database.flush()
            receipt = QueuedSubmissionMutationReceipt(
                queued_submission=row.to_resource(),
                queue_version=thread.queue_version,
            )
            if transaction_hook is not None:
                await transaction_hook(database, receipt)
            return receipt

    async def delete(
        self,
        *,
        organization_id: str,
        queued_submission_id: str,
        expected_version: int,
        replay: Callable[[AsyncSession], Awaitable[ThreadQueueMutationReceipt | None]] | None = None,
        transaction_hook: Callable[[AsyncSession, ThreadQueueMutationReceipt], Awaitable[None]] | None = None,
    ) -> ThreadQueueMutationReceipt:
        now = assume_utc(self._clock())
        async with transaction(self._sessions) as database:
            if replay is not None and (replayed := await replay(database)) is not None:
                return replayed
            scope = await _scope(database, organization_id=organization_id, queued_submission_id=queued_submission_id)
            thread = await _lock_thread(database, organization_id=organization_id, thread_id=scope)
            rows = await _lock_live(database, organization_id=organization_id, thread_id=scope)
            target = next((row for row in rows if row.id == queued_submission_id), None)
            if target is None:
                # A concurrent deletion can commit while this request waits for the Thread lock.
                await _load(database, organization_id=organization_id, queued_submission_id=queued_submission_id)
                raise QueuedSubmissionConflict("queued submission lifecycle changed")
            if target.version != expected_version:
                raise QueuedSubmissionConflict("queued submission version or lifecycle changed")
            deleted_position = target.position
            await database.execute(
                delete(IdempotencyEvidenceRecord).where(
                    IdempotencyEvidenceRecord.organization_id == organization_id,
                    IdempotencyEvidenceRecord.result_kind == "queued_submission",
                    IdempotencyEvidenceRecord.result_ref == target.id,
                )
            )
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
            receipt = ThreadQueueMutationReceipt(thread_id=thread.id, queue_version=thread.queue_version)
            if transaction_hook is not None:
                await transaction_hook(database, receipt)
            return receipt

    async def reorder(
        self,
        *,
        organization_id: str,
        thread_id: str,
        expected_queue_version: int,
        queued_submission_ids: tuple[str, ...],
        replay: Callable[[AsyncSession], Awaitable[ThreadQueueMutationReceipt | None]] | None = None,
        transaction_hook: Callable[[AsyncSession, ThreadQueueMutationReceipt], Awaitable[None]] | None = None,
    ) -> ThreadQueueMutationReceipt:
        now = assume_utc(self._clock())
        async with transaction(self._sessions) as database:
            thread = await _lock_thread(database, organization_id=organization_id, thread_id=thread_id)
            if replay is not None and (replayed := await replay(database)) is not None:
                return replayed
            rows = await _lock_live(database, organization_id=organization_id, thread_id=thread_id)
            if thread.queue_version != expected_queue_version:
                raise QueuedSubmissionConflict("Thread queue version changed")
            if set(queued_submission_ids) != {row.id for row in rows} or len(queued_submission_ids) != len(rows):
                raise QueuedSubmissionConflict("reorder must name every queued submission exactly once")
            current = tuple(row.id for row in rows)
            if current == queued_submission_ids:
                receipt = ThreadQueueMutationReceipt(thread_id=thread.id, queue_version=thread.queue_version)
                if transaction_hook is not None:
                    await transaction_hook(database, receipt)
                return receipt

            by_id = {row.id: row for row in rows}
            offset = len(rows) + max((row.position or 0 for row in rows), default=0)
            for index, entry_id in enumerate(queued_submission_ids, start=1):
                by_id[entry_id].position = offset + index
            await database.flush()
            for index, entry_id in enumerate(queued_submission_ids, start=1):
                by_id[entry_id].position = index
            thread.queue_version += 1
            thread.updated_at = now
            receipt = ThreadQueueMutationReceipt(thread_id=thread.id, queue_version=thread.queue_version)
            if transaction_hook is not None:
                await transaction_hook(database, receipt)
            return receipt

    async def _validate_inline_hook_destination(self, submission: ThreadRunSubmissionIntent) -> None:
        try:
            await self._inline_hooks.validate_destination(submission.hook_subscription)
        except InlineHookValidationError as error:
            raise QueuedSubmissionConflict(str(error)) from error

    async def _authorize_inline_hook(
        self,
        database: AsyncSession,
        *,
        organization_id: str,
        workspace_id: str,
        agent_id: str,
        principal: PrincipalRef,
        submission: ThreadRunSubmissionIntent,
    ) -> None:
        try:
            await self._inline_hooks.authorize(
                database,
                principal=principal,
                organization_id=organization_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                subscription=submission.hook_subscription,
            )
        except InlineHookValidationError as error:
            raise QueuedSubmissionConflict(str(error)) from error


def classify_thread_submission(
    *,
    thread: Thread,
    current: Run | None,
    head: Run | None,
    has_queued_submission: bool,
    waiting_resolution_requested: bool,
) -> ThreadSubmissionAdmission:
    """Select the spec-defined queue-if-busy branch from one detached snapshot."""

    if current is None:
        if thread.current_run_id is not None or head is not None or waiting_resolution_requested:
            raise QueuedSubmissionConflict("empty Thread state is inconsistent")
        return ThreadSubmissionAdmission.root
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
    if current.status in {RunStatus.failed, RunStatus.cancelled}:
        if has_queued_submission or (head is not None and head.status is RunStatus.waiting):
            return ThreadSubmissionAdmission.reject
        if head is not None and head.status is RunStatus.completed:
            return ThreadSubmissionAdmission.continuation
        if head is None:
            return ThreadSubmissionAdmission.root
        return ThreadSubmissionAdmission.reject
    if has_queued_submission or current.status in {RunStatus.accepted, RunStatus.running, RunStatus.waiting}:
        return ThreadSubmissionAdmission.queued
    if head is not None and head.status is RunStatus.completed:
        return ThreadSubmissionAdmission.continuation
    return ThreadSubmissionAdmission.reject


async def _scope(database: AsyncSession, *, organization_id: str, queued_submission_id: str) -> str:
    thread_id = await database.scalar(
        select(QueuedSubmissionRecord.thread_id).where(
            QueuedSubmissionRecord.organization_id == organization_id,
            QueuedSubmissionRecord.id == queued_submission_id,
        )
    )
    if thread_id is None:
        raise QueuedSubmissionConflict("queued submission was not found")
    return thread_id


async def _lock_thread(database: AsyncSession, *, organization_id: str, thread_id: str) -> ThreadRecord:
    thread = await database.scalar(
        select(ThreadRecord)
        .where(ThreadRecord.organization_id == organization_id, ThreadRecord.id == thread_id)
        .with_for_update()
    )
    if thread is None:
        raise QueuedSubmissionConflict("Thread was not found")
    return thread


async def _lock_live(
    database: AsyncSession,
    *,
    organization_id: str,
    thread_id: str,
) -> tuple[QueuedSubmissionRecord, ...]:
    return tuple(
        (
            await database.scalars(
                select(QueuedSubmissionRecord)
                .where(
                    QueuedSubmissionRecord.organization_id == organization_id,
                    QueuedSubmissionRecord.thread_id == thread_id,
                    QueuedSubmissionRecord.position.is_not(None),
                )
                .order_by(QueuedSubmissionRecord.position, QueuedSubmissionRecord.id)
                .with_for_update()
            )
        ).all()
    )


async def _load(
    database: AsyncSession,
    *,
    organization_id: str,
    queued_submission_id: str,
) -> QueuedSubmissionRecord:
    row = await database.scalar(
        select(QueuedSubmissionRecord).where(
            QueuedSubmissionRecord.organization_id == organization_id,
            QueuedSubmissionRecord.id == queued_submission_id,
        )
    )
    if row is None:
        raise QueuedSubmissionConflict("queued submission was not found")
    return row


async def _load_run_snapshot(database: AsyncSession, *, organization_id: str, run_id: str) -> RunRecord:
    run = await database.scalar(
        select(RunRecord).where(RunRecord.organization_id == organization_id, RunRecord.id == run_id)
    )
    if run is None:
        raise QueuedSubmissionConflict("Thread-selected Run was not found")
    return run


async def _load_workspace_id(database: AsyncSession, thread: ThreadRecord) -> str:
    workspace_id = await database.scalar(
        select(SessionRecord.workspace_id).where(
            SessionRecord.organization_id == thread.organization_id,
            SessionRecord.id == thread.session_id,
        )
    )
    if workspace_id is None:
        raise QueuedSubmissionConflict("Thread Session was not found")
    return workspace_id


async def _lock_entry(
    database: AsyncSession,
    *,
    organization_id: str,
    queued_submission_id: str,
) -> QueuedSubmissionRecord:
    row = await database.scalar(
        select(QueuedSubmissionRecord)
        .where(
            QueuedSubmissionRecord.organization_id == organization_id,
            QueuedSubmissionRecord.id == queued_submission_id,
        )
        .with_for_update()
    )
    if row is None:
        raise QueuedSubmissionConflict("queued submission was not found")
    return row


__all__ = [
    "QueuedSubmissionConflict",
    "QueuedSubmissionStore",
    "ThreadSubmissionAdmission",
    "classify_thread_submission",
]
