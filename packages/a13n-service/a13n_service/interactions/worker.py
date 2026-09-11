"""Bounded process-local admission of claimed RunAttempts."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Protocol

from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from anyio import CancelScope, Event, Semaphore, create_task_group, current_time, move_on_after, sleep
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.ids import new_object_id
from a13n_service.storage import is_database_contention, is_database_unavailable, short_session

from .attempts import AttemptContext
from .domain import RunAttemptYieldReason, RunStatus
from .models import RunRecord
from .run_control import RunAttemptControl
from .scheduling import AttemptScheduler, ClaimedAttempt, WorkerClaim

logger = logging.getLogger("a13n_service.interactions.worker")


class AttemptRunner(Protocol):
    async def run(
        self,
        context: AttemptContext,
        catalog: HarnessPluginFactoryCatalog,
        slot: WorkerCapacitySlot,
        register: Callable[[RunAttemptControl], Awaitable[None]],
    ) -> None: ...


class WorkerCapacitySlot:
    """A reservation that can be released by either failed construction or its executor."""

    def __init__(self, capacity: Semaphore) -> None:
        self._capacity = capacity
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._capacity.release()


class WorkerExecutionLoop:
    """Preflight before claim, admit before claim, and immediately start a local root task."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        scheduler: AttemptScheduler,
        catalog: HarnessPluginFactoryCatalog,
        attempts: AttemptRunner,
        *,
        build_id: str,
        queue_name: str,
        concurrency: int = 8,
        poll_seconds: float = 1,
        lease_seconds: float = 30,
        cleanup_seconds: float = 10,
        drain_seconds: float = 30,
    ) -> None:
        if concurrency < 1 or min(poll_seconds, lease_seconds, cleanup_seconds, drain_seconds) <= 0:
            raise ValueError("Worker execution bounds must be positive")
        self._sessions = sessions
        self._scheduler = scheduler
        self._catalog = catalog
        self._attempts = attempts
        self._build_id = build_id
        self._queue_name = queue_name
        self._poll_seconds = poll_seconds
        self._lease = timedelta(seconds=lease_seconds)
        self._cleanup = timedelta(seconds=cleanup_seconds)
        self._drain_seconds = drain_seconds
        self._capacity = Semaphore(concurrency)
        self._worker_id = new_object_id("wrk")
        self._draining = Event()
        self._handoffs_requested = Event()
        self._stopped = Event()
        self._controls: dict[str, RunAttemptControl] = {}
        self._scopes: dict[str, CancelScope] = {}
        self._drain_deadline = float("inf")
        self._admission_scope: CancelScope | None = None
        self._candidate_offset = 0

    def is_draining(self) -> bool:
        return self._draining.is_set()

    def begin_drain(self) -> None:
        """Stop admission immediately without waiting for active Run control locks."""
        if not self.is_draining():
            self._drain_deadline = current_time() + self._drain_seconds
            self._draining.set()
        if self._admission_scope is not None:
            self._admission_scope.deadline = self._drain_deadline
        for scope in tuple(self._scopes.values()):
            scope.deadline = self._drain_deadline

    async def drain(self) -> None:
        self.begin_drain()
        await self._handoffs_requested.wait()

    async def _request_handoffs_on_drain(self) -> None:
        await self._draining.wait()
        try:
            for control in tuple(self._controls.values()):
                # Each root retains its lease monitor until handoff or the hard deadline.
                with move_on_after(max(0, self._drain_deadline - current_time())):
                    try:
                        await control.request_handoff(RunAttemptYieldReason.service_drain)
                    except Exception:
                        logger.info("run_handoff_request_ended", extra={"run_id": control.current_context.run_id})
        finally:
            self._handoffs_requested.set()

    async def wait_stopped(self) -> None:
        await self._stopped.wait()

    async def run(self) -> None:
        try:
            async with create_task_group() as roots:
                roots.start_soon(self._request_handoffs_on_drain)
                while not self.is_draining():
                    productive = False
                    try:
                        with CancelScope(deadline=self._drain_deadline) as admission:
                            self._admission_scope = admission
                            for organization_id in await self._organizations():
                                if self.is_draining() or self._capacity.value == 0:
                                    break
                                claim = WorkerClaim(
                                    organization_id=organization_id,
                                    worker_id=self._worker_id,
                                    worker_build_id=self._build_id,
                                    lease_duration=self._lease,
                                    handoff_preference_window=self._lease,
                                )
                                candidates = await self._scheduler.scan(claim, queue_name=self._queue_name)
                                if not candidates:
                                    continue
                                for run_id in candidates:
                                    if self.is_draining() or self._capacity.value == 0:
                                        break
                                    self._capacity.acquire_nowait()
                                    slot = WorkerCapacitySlot(self._capacity)
                                    try:
                                        result = await self._scheduler.claim(run_id, claim)
                                        productive = productive or result is not None
                                        if isinstance(result, ClaimedAttempt):
                                            roots.start_soon(self._execute, self._context(result, claim), slot)
                                            slot = None
                                    finally:
                                        if slot is not None:
                                            slot.release()
                    except Exception as error:
                        contention = is_database_contention(error)
                        if not contention and not is_database_unavailable(error):
                            raise
                        productive = False
                        logger.warning(
                            "worker_database_contention" if contention else "worker_database_unavailable",
                            extra={"retry_seconds": self._poll_seconds},
                        )
                    finally:
                        self._admission_scope = None
                    if productive and self._capacity.value > 0:
                        await sleep(0)
                        continue
                    with move_on_after(self._poll_seconds):
                        await self._draining.wait()
        finally:
            self._stopped.set()

    async def _organizations(self) -> tuple[str, ...]:
        statement = (
            select(RunRecord.organization_id)
            .where(
                RunRecord.queue_name == self._queue_name,
                RunRecord.status.in_((RunStatus.accepted.value, RunStatus.running.value)),
            )
            .order_by(RunRecord.available_at, RunRecord.id)
            .offset(self._candidate_offset)
            .limit(128)
        )
        async with short_session(self._sessions) as session:
            organizations = tuple((await session.scalars(statement)).all())
        self._candidate_offset = self._candidate_offset + len(organizations) if len(organizations) == 128 else 0
        return tuple(dict.fromkeys(organizations))

    async def _execute(self, context: AttemptContext, slot: WorkerCapacitySlot) -> None:
        async def register(control: RunAttemptControl) -> None:
            self._controls[context.run_attempt_id] = control
            if self.is_draining():
                await control.request_handoff(RunAttemptYieldReason.service_drain)

        try:
            with CancelScope(deadline=self._drain_deadline) as scope:
                self._scopes[context.run_attempt_id] = scope
                logger.info(
                    "run_attempt_execution_started",
                    extra={
                        "run_id": context.run_id,
                        "run_attempt_id": context.run_attempt_id,
                        "attempt_number": context.attempt_number,
                    },
                )
                await self._attempts.run(context, self._catalog, slot, register)
        except Exception:
            logger.warning("run_attempt_execution_stopped", extra={"run_attempt_id": context.run_attempt_id})
        finally:
            self._controls.pop(context.run_attempt_id, None)
            self._scopes.pop(context.run_attempt_id, None)
            slot.release()

    def _context(self, result: ClaimedAttempt, claim: WorkerClaim) -> AttemptContext:
        attempt = result.attempt
        return AttemptContext(
            organization_id=claim.organization_id,
            thread_id=result.thread_id,
            run_id=attempt.run_id,
            run_attempt_id=attempt.id,
            attempt_number=attempt.attempt_number,
            lease_token=result.lease_token,
            worker_id=claim.worker_id,
            worker_build_id=claim.worker_build_id,
            lease_duration=self._lease,
            renewal_interval=self._lease / 3,
            renewal_timeout=self._lease / 6,
            reconciliation_timeout=self._lease / 6,
            cleanup_timeout=self._cleanup,
        )
