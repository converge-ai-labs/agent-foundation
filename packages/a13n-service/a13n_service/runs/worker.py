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
from a13n_service.infra.telemetry import Gauge, meter
from a13n_service.runs.attempts import AttemptControl, Lease, LeaseLost, renew
from a13n_service.runs.claim import claim
from a13n_service.runs.members import FALLBACK_BATCH, Members
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tool_executions.authority import heartbeat

logger = get_logger(__name__)

type Execute = Callable[[Runtime, Lease, AttemptControl], Coroutine[Any, Any, None]]

CLAIMS = meter.create_histogram(
    "a13n.worker.claim.duration",
    unit="s",
    description="Worker claim scans by claimed, empty or failed result",
    explicit_bucket_boundaries_advisory=(0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 15, 30),
)
SLOTS = Gauge("a13n.worker.slots", unit="{slot}", description="This worker's attempt slots by state")
REALTIME_SLOTS = Gauge("a13n.worker.realtime_slots", unit="{slot}", description="This worker's realtime slots by state")


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
        self.realtime_free = runtime.settings.worker.realtime_slots if runtime.realtime_bridge is not None else 0
        self.slot_released = asyncio.Event()
        self.running: dict[str, _Running] = {}
        self.stopping = False
        self.members: Members | None = None

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
        # Separate deployments/schemas sharing Redis must not count one another.
        scope = self.runtime.storage.engine.url.set(password=None).render_as_string(hide_password=True)
        self.members = Members(
            self.runtime.redis,
            scope,
            self.id,
            ttl=max(settings.lease_seconds, 3 * settings.scan_seconds),
            timeout=self.runtime.settings.redis.timeout,
        )
        logger.info("Worker started", extra={"host": socket.gethostname(), "build": self.build})
        self._report_slots()
        async with asyncio.TaskGroup() as group:
            group.create_task(self._supervise(), name="lease-supervisor")
            group.create_task(self._recover_shells(), name="shell-recovery")
            group.create_task(self.runtime.executions.routing.run(self.id), name="shell-control-routing")
            group.create_task(self.runtime.executions.output.run(), name="shell-output-cache")
            try:
                while not self.stopping:
                    if not self.runtime.executions.accepting:
                        await self._drain()
                        logger.error("Tool execution admission closed; forcing process exit after drain")
                        os._exit(1)
                    members = await self.members.refresh()
                    if self.free == 0 and self.realtime_free == 0:
                        self.slot_released.clear()
                        # Recheck failed tool admission even when every attempt slot remains occupied.
                        try:
                            await asyncio.wait_for(
                                self.slot_released.wait(),
                                timeout=min(settings.authority_seconds, settings.scan_seconds),
                            )
                        except TimeoutError:
                            pass
                        continue
                    saturated = claimed = failed = False
                    for realtime, requested in ((False, self.free), (True, self.realtime_free)):
                        if not requested:
                            continue
                        if members is None:
                            requested = min(requested, FALLBACK_BATCH)
                        sent = asyncio.get_running_loop().time()
                        try:
                            leases = await claim(
                                self.runtime,
                                worker_id=self.id,
                                worker_build=self.build,
                                limit=requested,
                                members=members if members is not None else 1,
                                realtime=realtime,
                            )
                        except Exception as error:
                            CLAIMS.record(asyncio.get_running_loop().time() - sent, {"result": "failed"})
                            # Running attempts keep their slots and leases; the next scan tries again.
                            logger.warning(
                                "Claim failed",
                                extra={
                                    "error_type": type(error).__name__,
                                    "exception_details": exception_details(error),
                                },
                            )
                            failed = True
                            continue
                        CLAIMS.record(
                            asyncio.get_running_loop().time() - sent, {"result": "claimed" if leases else "empty"}
                        )
                        for lease in leases:
                            if realtime:
                                self.realtime_free -= 1
                            else:
                                self.free -= 1
                            control = AttemptControl(
                                deadline=sent + settings.lease_seconds, renewal_margin=settings.lease_seconds / 3
                            )
                            task = group.create_task(self._attempt(lease, control), name=f"attempt-{lease.attempt_id}")
                            self.running[lease.attempt_id] = _Running(lease, control, task)
                        self._report_slots()
                        saturated |= len(leases) == requested
                        if leases:
                            claimed = True
                            logger.info(
                                "Worker claim batch",
                                extra={
                                    "members": members,
                                    "claimed": len(leases),
                                    "free_slots": self.realtime_free if realtime else self.free,
                                    "realtime": realtime,
                                },
                            )
                    if failed or (claimed and members != 1):
                        # Wake markers must not let one worker immediately consume another share.
                        await asyncio.sleep(settings.scan_seconds)
                    elif not saturated:
                        await wait_for_wake(self.runtime.redis, timeout=settings.scan_seconds)
            except asyncio.CancelledError:
                await self._drain()
                raise
            finally:
                await self.members.leave()

    async def _drain(self) -> None:
        self.stopping = True
        self.runtime.executions.stop_admission()
        if self.members is not None:
            await self.members.leave()
        for running in self.running.values():
            running.control.handoff.set()
        tasks = [running.task for running in self.running.values()]
        if tasks:
            await asyncio.wait(tasks, timeout=self.runtime.settings.worker.drain_seconds)
        # Native supervisors detach before attempt cancellation can reach their resources.
        self.runtime.executions.yield_recoverable()
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
                if lease.realtime:
                    self.realtime_free += 1
                else:
                    self.free += 1
                self._report_slots()
                self.slot_released.set()

    def _report_slots(self) -> None:
        slots = self.runtime.settings.worker.slots
        SLOTS.set(self.free, {"state": "free"})
        SLOTS.set(slots - self.free, {"state": "busy"})
        realtime_slots = self.runtime.settings.worker.realtime_slots if self.runtime.realtime_bridge is not None else 0
        REALTIME_SLOTS.set(self.realtime_free, {"state": "free"})
        REALTIME_SLOTS.set(realtime_slots - self.realtime_free, {"state": "busy"})

    async def _recover_shells(self) -> None:
        while not self.stopping:
            try:
                await self.runtime.executions.recover(self.id)
            except Exception as error:
                logger.warning("Shell recovery scan failed", extra={"error_type": type(error).__name__})
            await asyncio.sleep(self.runtime.settings.worker.scan_seconds)

    async def _supervise(self) -> None:
        """Every authority interval, one heartbeat polls cancellation and each principal's authority for all running
        attempts and renews the leases with no more than two thirds left, independently of the Harness tasks.

        A renewal not confirmed within one authority interval has failed. Once an attempt's lease has missed its
        renewal, stop it: another worker may take over once the lease expires, and a stale attempt must not keep
        dispatching.
        """
        settings = self.runtime.settings.worker
        loop = asyncio.get_running_loop()
        next_poll = loop.time() + settings.authority_seconds
        while True:
            await asyncio.sleep(max(0, next_poll - loop.time()))
            sent = loop.time()
            # Poll work consumes the interval; a timed-out renewal must not add another full sleep.
            next_poll = sent + settings.authority_seconds
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
                if deadline - loop.time() <= settings.lease_seconds / 3 and not self.runtime.executions.yield_shell(
                    execution_id
                ):
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
