"""Bounded process-local admission and supervision of claimed RunAttempts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from a13n_logging import get_logger
from anyio import Event, create_task_group, move_on_after

from .attempt_executor import CapacitySlot
from .attempts import AttemptAuthorityError
from .domain import RunAttemptYieldReason
from .objects import StaleStateWriter
from .scheduling import AttemptScheduler, ClaimedAttempt, RunCandidate, ScanPosition, WorkerClaim

logger = get_logger(__name__)


class ManagedAttempt(Protocol):
    """One claimed Attempt, including preparation and its complete executor lifetime."""

    async def run(self) -> None: ...

    async def request_handoff(self, reason: RunAttemptYieldReason) -> None: ...


class AttemptLauncher(Protocol):
    """An exact preflighted Runtime that can construct an Attempt without I/O."""

    def create(self, claimed: ClaimedAttempt, capacity_slot: CapacitySlot) -> ManagedAttempt: ...


class ExecutionPreflight(Protocol):
    """Prepare the selected Runtime before any relational execution claim."""

    async def prepare(self, candidate: RunCandidate) -> AttemptLauncher | None: ...


@dataclass(frozen=True, slots=True)
class WorkerIdentity:
    worker_id: str
    generation: str
    build_id: str

    def __post_init__(self) -> None:
        if not all((self.worker_id, self.generation, self.build_id)):
            raise ValueError("Worker identity fields must not be empty")


class _Capacity:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def reserve(self) -> _Slot | None:
        if self.used >= self.limit:
            return None
        self.used += 1
        return _Slot(self)


class _Slot:
    def __init__(self, capacity: _Capacity) -> None:
        self._capacity = capacity
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._capacity.used -= 1


class WorkerExecutionLoop:
    """Use the same scan/claim path in an on-demand Worker or lock-scoped Runner."""

    def __init__(
        self,
        scheduler: AttemptScheduler,
        preflight: ExecutionPreflight,
        *,
        identity: WorkerIdentity,
        lease_duration: timedelta,
        handoff_preference_window: timedelta,
        concurrency: int = 8,
        scan_limit: int = 32,
        poll_interval_seconds: float = 0.5,
        queue_names: tuple[str, ...] = (),
        runtime_lock_digest: str | None = None,
    ) -> None:
        if not 1 <= concurrency <= 1024 or not 1 <= scan_limit <= 1024:
            raise ValueError("Worker capacity and scan limit must be between 1 and 1024")
        if poll_interval_seconds <= 0:
            raise ValueError("Worker poll interval must be positive")
        if lease_duration <= timedelta(0) or handoff_preference_window <= timedelta(0):
            raise ValueError("Worker lease and handoff preference durations must be positive")
        self._scheduler = scheduler
        self._preflight = preflight
        self._identity = identity
        self._lease_duration = lease_duration
        self._handoff_preference_window = handoff_preference_window
        self._capacity = _Capacity(concurrency)
        self._scan_limit = scan_limit
        self._poll_interval_seconds = poll_interval_seconds
        self._queue_names = queue_names
        self._runtime_lock_digest = runtime_lock_digest
        self._draining = Event()
        self._started = Event()
        self._stopped = Event()
        self._active: dict[str, ManagedAttempt] = {}
        self._handoff_reason = RunAttemptYieldReason.service_drain
        self._position: ScanPosition | None = None
        self._used = False

    @property
    def active_count(self) -> int:
        return self._capacity.used

    def is_draining(self) -> bool:
        return self._draining.is_set()

    async def wait_started(self) -> None:
        await self._started.wait()

    async def wait_stopped(self) -> None:
        await self._stopped.wait()

    async def drain(self, reason: RunAttemptYieldReason = RunAttemptYieldReason.service_drain) -> None:
        """Gate new claims first; active owners retain their leases until they finish."""

        self._handoff_reason = reason
        self._draining.set()
        for attempt in tuple(self._active.values()):
            await attempt.request_handoff(reason)

    async def run(self) -> None:
        if self._used:
            raise RuntimeError("A Worker execution loop cannot be restarted")
        self._used = True
        try:
            async with create_task_group() as tasks:
                self._started.set()
                while not self.is_draining():
                    if self._capacity.used < self._capacity.limit:
                        candidates = await self._scheduler.discover(
                            worker_build_id=self._identity.build_id,
                            handoff_preference_window=self._handoff_preference_window,
                            limit=self._scan_limit,
                            after=self._position,
                            queue_names=self._queue_names,
                            runtime_lock_digest=self._runtime_lock_digest,
                        )
                        for candidate in candidates:
                            if self.is_draining() or self._capacity.used >= self._capacity.limit:
                                break
                            self._position = candidate.position
                            launcher = await self._preflight.prepare(candidate)
                            if launcher is None or self.is_draining():
                                continue
                            slot = self._capacity.reserve()
                            if slot is None:
                                break
                            transferred = False
                            try:
                                claimed = await self._scheduler.claim(
                                    candidate.run_id,
                                    WorkerClaim(
                                        tenant_id=candidate.tenant_id,
                                        worker_id=self._identity.worker_id,
                                        worker_generation=self._identity.generation,
                                        worker_build_id=self._identity.build_id,
                                        runtime_lock_digest=candidate.runtime_lock_digest,
                                        lease_duration=self._lease_duration,
                                        handoff_preference_window=self._handoff_preference_window,
                                    ),
                                )
                                if isinstance(claimed, ClaimedAttempt):
                                    attempt = launcher.create(claimed, slot)
                                    self._active[claimed.attempt.id] = attempt
                                    tasks.start_soon(self._execute, claimed, attempt, slot)
                                    transferred = True
                            finally:
                                if not transferred:
                                    slot.release()
                        if len(candidates) < self._scan_limit:
                            self._position = None
                    with move_on_after(self._poll_interval_seconds):
                        await self._draining.wait()
                # The process owner bounds drain. Task-group exit keeps renewal alive meanwhile.
        finally:
            self._stopped.set()

    async def _execute(self, claimed: ClaimedAttempt, attempt: ManagedAttempt, slot: _Slot) -> None:
        try:
            if self.is_draining():
                await attempt.request_handoff(self._handoff_reason)
            await attempt.run()
        except* (AttemptAuthorityError, StaleStateWriter):
            logger.info(
                "run_attempt_authority_lost",
                extra={"run_id": claimed.attempt.run_id, "run_attempt_id": claimed.attempt.id},
            )
        finally:
            self._active.pop(claimed.attempt.id, None)
            slot.release()


__all__ = ["AttemptLauncher", "ExecutionPreflight", "ManagedAttempt", "WorkerExecutionLoop", "WorkerIdentity"]
