"""The worker process loop: reserve a slot, claim, execute each attempt under a supervised lease.

The periodic scan is the mechanism; the Redis marker only makes it run sooner. A full worker leaves markers
for others and rescans when a slot frees. One supervisor renews the leases of all running attempts. Shutdown
stops claiming, asks running attempts to hand off at their next safe boundary and waits up to the drain
deadline, still renewing; unfinished attempts then rely on lease expiry.
"""

import asyncio
import os
import socket
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

from a13n_logging import exception_details, get_logger, log_context

from a13n_service.infra.ids import new_object_id
from a13n_service.infra.redis import wait_for_wake
from a13n_service.infra.telemetry import Gauge
from a13n_service.runs.attempts import AttemptControl, Lease, LeaseLost, renew
from a13n_service.runs.claim import claim
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tool_executions.authority import heartbeat

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
            try:
                await self._run()
            except Exception as error:
                # A dead worker loop cannot renew or fence its retained invocations.
                # This also covers failures from TaskGroup children and shutdown drain.
                logger.error(
                    "Worker failed; forcing process exit",
                    extra={"error_type": type(error).__name__, "exception_details": exception_details(error)},
                )
                os._exit(1)

    async def _run(self) -> None:
        settings = self.runtime.settings.worker
        logger.info("Worker started", extra={"host": socket.gethostname(), "build": self.build})
        self._report_slots()
        async with asyncio.TaskGroup() as group:
            group.create_task(self._supervise(), name="lease-supervisor")
            group.create_task(self.runtime.executions.routing.run(self.id), name="shell-control-routing")
            group.create_task(self.runtime.executions.output.run(), name="shell-output-cache")
            try:
                while not self.stopping:
                    if not self.runtime.executions.accepting:
                        await self._drain()
                        logger.error("Tool execution admission closed; forcing process exit after drain")
                        os._exit(1)
                    if self.free == 0:
                        self.slot_released.clear()
                        # Recheck failed tool admission even when every attempt slot remains occupied.
                        try:
                            await asyncio.wait_for(self.slot_released.wait(), timeout=settings.authority_seconds)
                        except TimeoutError:
                            pass
                        continue
                    requested = self.free
                    sent = asyncio.get_running_loop().time()
                    try:
                        leases = await claim(self.runtime, worker_id=self.id, worker_build=self.build, limit=requested)
                    except Exception as error:
                        # Running attempts keep their slots and leases; the next scan tries again.
                        logger.warning(
                            "Claim failed",
                            extra={"error_type": type(error).__name__, "exception_details": exception_details(error)},
                        )
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
        self.runtime.executions.stop_admission()
        for running in self.running.values():
            running.control.handoff.set()
        tasks = [running.task for running in self.running.values()]
        if tasks:
            await asyncio.wait(tasks, timeout=self.runtime.settings.worker.drain_seconds)
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        policy = self.runtime.settings.tool_executions
        join = asyncio.create_task(self.runtime.executions.join())
        try:
            done, _ = await asyncio.wait({join}, timeout=policy.shutdown_grace_seconds)
            if done:
                join.result()
        finally:
            join.cancel()
            await asyncio.gather(join, return_exceptions=True)
        # Cancellation is local and non-blocking, even when persistence is unavailable. Give publication
        # and attempt cleanup one shared finalization window, then let control reconcile any missing result.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + policy.finalization_seconds
        pending_executions = await self.runtime.executions.close(
            timeout_seconds=policy.finalization_seconds,
            reason="Worker shutdown interrupted the tool; external effects may have completed.",
        )
        if tasks:
            await asyncio.wait(tasks, timeout=max(0, deadline - loop.time()))
        if pending_executions or any(not task.done() for task in tasks):
            logger.error(
                "Worker finalization exceeded shutdown deadline; forcing process exit",
                extra={"attempts": len(self.running), "executions": len(pending_executions)},
            )
            os._exit(1)

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
                logger.error(
                    "Attempt crashed",
                    extra={"error_type": type(error).__name__, "exception_details": exception_details(error)},
                )
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
            sent = loop.time()
            try:
                async with asyncio.timeout(settings.authority_seconds):
                    cancellations, renewed = await heartbeat(self.runtime, self.id)
                for thread_id, execution_id in cancellations:
                    self.runtime.executions.request_cancellation(thread_id, execution_id)
            except Exception as error:
                logger.warning("Tool execution heartbeat failed", extra={"error_type": type(error).__name__})
                renewed = set()
            for record in self.runtime.executions.active:
                execution_id = record.execution_id
                if execution_id in renewed:
                    self.runtime.executions.lease_deadlines[execution_id] = sent + settings.lease_seconds
                deadline = self.runtime.executions.lease_deadlines[execution_id]
                if deadline - loop.time() <= settings.lease_seconds / 3:
                    self.runtime.executions.request_cancellation(
                        record.thread_id,
                        execution_id,
                        reason="Tool execution lease renewal failed; external effects may have completed.",
                    )
                if loop.time() >= deadline:
                    # A successful heartbeat may still omit an expired, missing or reconciled owner.
                    # Another execution's renewal never extends this invocation's fencing window.
                    logger.error("Tool execution owner lease expired; forcing process exit")
                    os._exit(1)
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
                logger.warning(
                    "Lease renewal failed",
                    extra={"error_type": type(error).__name__, "exception_details": exception_details(error)},
                )
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
