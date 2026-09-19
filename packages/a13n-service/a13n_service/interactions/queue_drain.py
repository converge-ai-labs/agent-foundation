"""Shared post-terminal queue consumption for Workers and periodic Control scans."""

from __future__ import annotations

import anyio
from a13n_logging import get_logger
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from a13n_service.application_errors import ApplicationError
from a13n_service.background import Sweep
from a13n_service.iam.operation import authorization_operation
from a13n_service.storage import ObjectStoreError, short_session
from a13n_service.temporal import Clock, assume_utc, utc_now

from .control_domain import QueuedSubmission
from .control_models import QueuedSubmissionRecord
from .domain import Thread
from .models import RunRecord, SessionRecord, ThreadRecord
from .objects import RunObjectError
from .queue_commands import QueuedRunCommands

logger = get_logger(__name__)


class QueueDrain:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        commands: QueuedRunCommands,
        *,
        batch_limit: int = 64,
        item_timeout_seconds: float = 30,
        clock: Clock = utc_now,
    ) -> None:
        if not 1 <= batch_limit <= 1000 or item_timeout_seconds <= 0:
            raise ValueError("queue recovery bounds must be positive")
        self._sessions = sessions
        self._commands = commands
        self._limit = batch_limit
        self._timeout = item_timeout_seconds
        self._clock = clock
        self._after_id = ""

    async def scan(self) -> Sweep:
        async with short_session(self._sessions) as database:
            rows = (
                await database.execute(
                    _consumable_threads()
                    .where(QueuedSubmissionRecord.id > self._after_id)
                    .order_by(QueuedSubmissionRecord.id)
                    .limit(self._limit)
                )
            ).all()
            candidates = tuple(
                (queue.to_resource(), thread.to_resource(), workspace) for queue, thread, workspace in rows
            )
        if not candidates:
            self._after_id = ""
            return Sweep()
        completed = deferred = 0
        oldest_age = 0.0
        for queued, thread, workspace_id in candidates:
            # Advance before external work so a hung or deferred head cannot starve later Threads.
            self._after_id = queued.queued_submission_id
            oldest_age = max(oldest_age, (assume_utc(self._clock()) - queued.created_at).total_seconds())
            if await self._consume(queued=queued, thread=thread, workspace_id=workspace_id):
                completed += 1
            else:
                deferred += 1
        return Sweep(examined=len(candidates), completed=completed, deferred=deferred, oldest_age_seconds=oldest_age)

    @authorization_operation
    async def consume_thread(self, *, organization_id: str, thread_id: str) -> bool:
        """Consume at most one eligible head using fresh committed Thread state."""
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    _consumable_threads().where(
                        ThreadRecord.organization_id == organization_id,
                        ThreadRecord.id == thread_id,
                    )
                )
            ).one_or_none()
            if row is None:
                return False
            queued, thread, workspace_id = row[0].to_resource(), row[1].to_resource(), row[2]
        return await self._consume(queued=queued, thread=thread, workspace_id=workspace_id)

    async def _consume(self, *, queued: QueuedSubmission, thread: Thread, workspace_id: str) -> bool:
        try:
            with anyio.fail_after(self._timeout):
                receipt = await self._commands.recover_queued(workspace_id=workspace_id, thread=thread, queued=queued)
        except (ApplicationError, RunObjectError, ObjectStoreError, TimeoutError) as error:
            logger.info(
                "queued_submission_drain_deferred",
                extra={"thread_id": thread.id, "error_type": type(error).__name__},
            )
            return False
        logger.info(
            "queued_submission_drained",
            extra={
                "thread_id": thread.id,
                "queued_submission_id": queued.queued_submission_id,
                "outcome": receipt.outcome,
            },
        )
        return True


def _consumable_threads():
    current, head = aliased(RunRecord), aliased(RunRecord)
    return (
        select(QueuedSubmissionRecord, ThreadRecord, SessionRecord.workspace_id)
        .join(ThreadRecord, ThreadRecord.id == QueuedSubmissionRecord.thread_id)
        .join(SessionRecord, SessionRecord.id == ThreadRecord.session_id)
        .join(current, current.id == ThreadRecord.current_run_id)
        .outerjoin(head, head.id == ThreadRecord.head_run_id)
        .where(
            QueuedSubmissionRecord.organization_id == ThreadRecord.organization_id,
            SessionRecord.organization_id == ThreadRecord.organization_id,
            current.organization_id == ThreadRecord.organization_id,
            QueuedSubmissionRecord.position == 1,
            current.status.in_(("completed", "failed", "cancelled")),
            or_(ThreadRecord.head_run_id.is_(None), head.status == "completed"),
        )
    )
