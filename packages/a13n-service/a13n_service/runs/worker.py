"""The worker process loop: reserve a slot, claim, execute each attempt under a supervised lease.

The periodic scan is the mechanism; the Redis marker only makes it run sooner. A full worker leaves markers
for others and rescans when a slot frees. One supervisor renews the leases of all running attempts. Shutdown
stops claiming, asks running attempts to hand off at their next safe boundary and waits up to the drain
deadline, still renewing; unfinished attempts then rely on lease expiry.
"""

import asyncio
import socket
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

from a13n_logging import get_logger, log_context

from a13n_service.infra.ids import new_object_id
from a13n_service.infra.redis import wait_for_wake
from a13n_service.infra.telemetry import Gauge
from a13n_service.runs.attempts import AttemptControl, Lease, LeaseLost, renew
from a13n_service.runs.claim import claim
from a13n_service.runs.runtime import Runtime

logger = get_logger(__name__)

type Execute = Callable[[Runtime, Lease, AttemptControl], Coroutine[Any, Any, None]]

SLOTS = Gauge("a13n.worker.slots", unit="{slot}", description="This worker's attempt slots by state")


@dataclass(frozen=True, slots=True)
class _Running:
    lease: Lease
    control: AttemptControl
    task: asyncio.Task[None]


class Worker:
    def __init__(self, runtime: Runtime, execute: Execute):
        self.runtime, self.execute = runtime, execute
        # One ID per worker incarnation, recorded on each attempt; the start log maps it to its host.
        self.id = new_object_id("wrk")
        self.build = version("a13n-service")
        self.free = runtime.settings.worker.slots
        self.slot_released = asyncio.Event()
        self.running: dict[str, _Running] = {}
        self.stopping = False

    async def run(self) -> None:
        with log_context(worker_id=self.id):
            await self._run()

    async def _run(self) -> None:
        settings = self.runtime.settings.worker
        logger.info("Worker started", extra={"host": socket.gethostname(), "build": self.build})
        self._report_slots()
        async with asyncio.TaskGroup() as group:
            group.create_task(self._supervise(), name="lease-supervisor")
            try:
                while not self.stopping:
                    if self.free == 0:
                        self.slot_released.clear()
                        await self.slot_released.wait()
                        continue
                    requested = self.free
                    sent = asyncio.get_running_loop().time()
                    try:
                        leases = await claim(self.runtime, worker_id=self.id, worker_build=self.build, limit=requested)
                    except Exception as error:
                        # Running attempts keep their slots and leases; the next scan tries again.
                        logger.warning("Claim failed", extra={"error_type": type(error).__name__})
                        await asyncio.sleep(settings.scan_seconds)
                        continue
                    for lease in leases:
                        self.free -= 1
                        control = AttemptControl(
                            deadline=sent + settings.lease_seconds, renewal_margin=settings.lease_seconds / 3
                        )
                        task = group.create_task(self._attempt(lease, control), name=f"attempt-{lease.attempt_id}")
                        self.running[lease.attempt_id] = _Running(lease, control, task)
                    self._report_slots()
                    if len(leases) < requested:
                        await wait_for_wake(self.runtime.redis, timeout=settings.scan_seconds)
            except asyncio.CancelledError:
                await self._drain()
                raise

    async def _drain(self) -> None:
        self.stopping = True
        for running in self.running.values():
            running.control.handoff.set()
        tasks = [running.task for running in self.running.values()]
        if tasks:
            await asyncio.wait(tasks, timeout=self.runtime.settings.worker.drain_seconds)

    async def _attempt(self, lease: Lease, control: AttemptControl) -> None:
        # Every record of the attempt, the Harness's included, names its run and attempt.
        with log_context(run_id=lease.run_id, attempt_id=lease.attempt_id):
            try:
                await self.execute(self.runtime, lease, control)
            except LeaseLost:
                logger.info("Attempt lost its lease")
            except asyncio.CancelledError:
                logger.info("Attempt stopped")
                raise  # The TaskGroup ignores a cancelled attempt, so cancelling one never stops the worker.
            except Exception as error:
                # Execution seals its own failures; anything escaping is left to lease expiry and recovery.
                logger.exception("Attempt crashed", extra={"error_type": type(error).__name__})
            finally:
                self.running.pop(lease.attempt_id, None)
                self.free += 1
                self._report_slots()
                self.slot_released.set()

    def _report_slots(self) -> None:
        slots = self.runtime.settings.worker.slots
        SLOTS.set(self.free, {"state": "free"})
        SLOTS.set(slots - self.free, {"state": "busy"})

    async def _supervise(self) -> None:
        """Every authority interval, one heartbeat polls cancellation and each principal's authority for all running
        attempts and renews the leases with no more than two thirds left, independently of the Harness tasks.

        A renewal not confirmed within one authority interval has failed. Once an attempt's lease has missed its
        renewal, stop it: another worker may take over once the lease expires, and a stale attempt must not keep
        dispatching.
        """
        settings = self.runtime.settings.worker
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(settings.authority_seconds)
            attempts = list(self.running.values())
            if not attempts:
                continue
            sent = loop.time()
            due = {
                running.lease.attempt_id
                for running in attempts
                if running.control.deadline - sent <= settings.lease_seconds * 2 / 3
            }
            try:
                async with asyncio.timeout(settings.authority_seconds):
                    stops, extended = await renew(
                        self.runtime.storage,
                        self.runtime.access,
                        [running.lease for running in attempts],
                        extend=due,
                        seconds=settings.lease_seconds,
                    )
            except Exception as error:
                logger.warning("Lease renewal failed", extra={"error_type": type(error).__name__})
                stops, extended = {}, set()
            for running in attempts:
                attempt_id, control = running.lease.attempt_id, running.control
                if attempt_id not in self.running:
                    continue  # It ended while the heartbeat was in flight.
                if attempt_id in extended:
                    control.deadline = sent + settings.lease_seconds
                if attempt_id in stops:
                    control.stop(stops[attempt_id])
                # Cancel once: cancelling again would interrupt the attempt's own cleanup.
                if control.renewal_missed() and not running.task.cancelling():
                    running.task.cancel()
