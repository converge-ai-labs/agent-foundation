"""Periodic post-terminal drain from retained Thread and queue authority."""

from __future__ import annotations

import logging

import anyio
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from a13n_service.application_errors import ApplicationError
from a13n_service.background import Sweep
from a13n_service.storage import ObjectStoreError, short_session
from a13n_service.temporal import Clock, assume_utc, utc_now

from .commands import InteractionCommands
from .control_models import QueuedSubmissionRecord
from .models import RunRecord, SessionRecord, ThreadRecord
from .objects import RunObjectError

logger = logging.getLogger("a13n_service.interactions.recovery")


class QueueRecovery:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        commands: InteractionCommands,
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
        current, head = aliased(RunRecord), aliased(RunRecord)
        async with short_session(self._sessions) as database:
            rows = (
                await database.execute(
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
                        QueuedSubmissionRecord.id > self._after_id,
                        current.status.in_(("completed", "failed", "cancelled")),
                        or_(ThreadRecord.head_run_id.is_(None), head.status == "completed"),
                    )
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
            try:
                with anyio.fail_after(self._timeout):
                    await self._commands.recover_queued(
                        workspace_id=workspace_id,
                        thread=thread,
                        queued=queued,
                    )
            except (ApplicationError, RunObjectError, ObjectStoreError, TimeoutError):
                deferred += 1
                logger.info(
                    "queued_submission_recovery_deferred",
                    extra={"event": "queued_submission_recovery_deferred", "thread_id": thread.id},
                )
            else:
                completed += 1
        return Sweep(examined=len(candidates), completed=completed, deferred=deferred, oldest_age_seconds=oldest_age)
