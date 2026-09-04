"""Structured process-local lifetime for one successfully claimed RunAttempt."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from a13n_harness import EnvironmentAccess, EnvironmentMount
from anyio import TASK_STATUS_IGNORED, CancelScope, create_task_group, fail_after, move_on_after, sleep
from anyio.abc import TaskStatus

from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.environments.runtime import prepare_run_environment
from a13n_service.storage import short_session

from .attempts import (
    AttemptAuthorityError,
    AttemptContext,
    AttemptMutationReceipt,
    AttemptPreparationRejected,
)
from .harness_results import HarnessOutcomeAdapter, RunTerminalCommitter, RunTerminalReceipt
from .harness_runtime import HarnessDriver, HarnessInvocation, NoHarnessEnvironment, SingleHarnessEnvironment
from .models import RunRecord
from .run_control import RunAttemptControl


class AttemptPreparer[OutputT](Protocol):
    """Perform non-authoritative dependency preflight and reconstruct one invocation."""

    async def prepare(self, context: AttemptContext) -> HarnessInvocation[OutputT]: ...


class ControlWakeupSource(Protocol):
    """Deliver opaque reconciliation hints without exposing their payload to control."""

    async def receive(self) -> object: ...

    async def acknowledge(self, signal: object) -> None: ...


class AttemptCleanup(Protocol):
    """Close Attempt-scoped process-local resources within the caller's bound."""

    async def close(
        self,
        context: AttemptContext,
        control: RunAttemptControl,
        driver: HarnessDriver,
    ) -> None: ...


class CapacitySlot(Protocol):
    """One capacity reservation held for the complete executor lifetime."""

    def release(self) -> None: ...


class LeaseMonitor:
    """Renew the exact Attempt and directly fence control when authority is lost."""

    def __init__(self, context: AttemptContext, control: RunAttemptControl) -> None:
        self._context = context
        self._control = control

    async def run(self, *, task_status: TaskStatus[None] = TASK_STATUS_IGNORED) -> None:
        task_status.started()
        while True:
            await sleep(self._context.renewal_interval.total_seconds())
            try:
                with fail_after(self._context.renewal_timeout.total_seconds()):
                    await self._control.renew_lease()
            except Exception:
                await self._control.authority_lost()
                raise


class ControlWatcher:
    """Turn Redis hints into bounded durable reconciliation and late acknowledgement."""

    def __init__(
        self,
        context: AttemptContext,
        control: RunAttemptControl,
        wakeups: ControlWakeupSource,
    ) -> None:
        self._context = context
        self._control = control
        self._wakeups = wakeups

    async def run(self, *, task_status: TaskStatus[None] = TASK_STATUS_IGNORED) -> None:
        await self._reconcile()
        task_status.started()
        while True:
            signal = await self._wakeups.receive()
            await self._reconcile()
            with fail_after(self._context.reconciliation_timeout.total_seconds()):
                await self._wakeups.acknowledge(signal)

    async def _reconcile(self) -> None:
        with fail_after(self._context.reconciliation_timeout.total_seconds()):
            await self._control.reconcile()


class RunAttemptExecutor[OutputT]:
    """Supervise one Harness run, exactly two child tasks, cleanup, and capacity."""

    def __init__(
        self,
        *,
        context: AttemptContext,
        control: RunAttemptControl,
        driver: HarnessDriver,
        preparer: AttemptPreparer[OutputT],
        wakeups: ControlWakeupSource,
        adapter: HarnessOutcomeAdapter,
        committer: RunTerminalCommitter,
        cleanup: AttemptCleanup,
        capacity_slot: CapacitySlot,
        environments: EnvironmentLifecycle,
    ) -> None:
        if control.current_context is not context:
            raise ValueError("executor, control, and monitor must share one Attempt context")
        self._context = context
        self._control = control
        self._driver = driver
        self._preparer = preparer
        self._wakeups = wakeups
        self._adapter = adapter
        self._committer = committer
        self._cleanup = cleanup
        self._capacity_slot = capacity_slot
        self._environments = environments

    async def run(self) -> RunTerminalReceipt | AttemptMutationReceipt | AttemptPreparationRejected:
        finalization: RunTerminalReceipt | AttemptMutationReceipt | AttemptPreparationRejected | None = None
        environment = None
        try:
            async with create_task_group() as tasks:
                self._control.bind_executor(self._driver, tasks.cancel_scope.cancel)
                try:
                    await tasks.start(LeaseMonitor(self._context, self._control).run)
                    await tasks.start(ControlWatcher(self._context, self._control, self._wakeups).run)
                    invocation = await self._preparer.prepare(self._context)
                    environment = await prepare_run_environment(self._environments, self._context)
                    if environment is None:
                        invocation = replace(invocation, environment=NoHarnessEnvironment())
                    else:
                        async with short_session(self._environments.sessions) as session:
                            run = await session.get(RunRecord, self._context.run_id)
                            if run is None or run.environment_access is None:
                                raise AttemptAuthorityError("Run Environment selection is missing")
                            access = run.environment_access
                        invocation = replace(
                            invocation,
                            environment=SingleHarnessEnvironment(
                                EnvironmentMount(environment, access=EnvironmentAccess(access))
                            ),
                        )
                    decision = await self._control.commit_preparation()
                    if isinstance(decision, AttemptPreparationRejected):
                        finalization = decision
                    else:
                        candidate = await self._driver.run(invocation, preparation=decision)
                        finalization = await self._control.finalize(
                            candidate,
                            adapter=self._adapter,
                            committer=self._committer,
                        )
                finally:
                    with CancelScope(shield=True):
                        await self._control.close_admission()
                        tasks.cancel_scope.cancel()
            if finalization is None:
                raise AttemptAuthorityError("Attempt executor stopped without an authoritative finalization")
            return finalization
        finally:
            try:
                with move_on_after(self._context.cleanup_timeout.total_seconds(), shield=True):
                    if environment is not None:
                        await environment.close()
                    await self._cleanup.close(self._control.current_context, self._control, self._driver)
            finally:
                self._capacity_slot.release()


__all__ = [
    "AttemptCleanup",
    "AttemptPreparer",
    "CapacitySlot",
    "ControlWakeupSource",
    "ControlWatcher",
    "LeaseMonitor",
    "RunAttemptExecutor",
]
