"""Structured process-local lifetime for one successfully claimed RunAttempt."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from a13n_harness import SafeFailure
from a13n_logging import get_logger
from anyio import TASK_STATUS_IGNORED, CancelScope, create_task_group, fail_after, sleep
from anyio.abc import TaskStatus

from a13n_service.skills.runtime import SkillRuntimeError
from a13n_service.storage.object_store import ObjectStoreUnavailable

from .attempts import (
    AttemptAuthorityError,
    AttemptContext,
    AttemptMutationReceipt,
    AttemptPreparationAccepted,
    AttemptPreparationRejected,
)
from .harness_results import AttemptCommitter, AttemptOutcome, HarnessOutcomeAdapter
from .harness_runtime import HarnessDriver, HarnessInvocation
from .run_control import RunAttemptControl
from .state_admission import StateClaimExhausted

logger = get_logger(__name__)


class AttemptPreparer[OutputT](Protocol):
    """Perform non-authoritative dependency preflight and reconstruct one invocation."""

    async def claim_state_writer(self) -> None: ...

    async def validate_dependencies(self, context: AttemptContext) -> None: ...

    def open_runtime(self, context: AttemptContext) -> AbstractAsyncContextManager[HarnessInvocation[OutputT]]: ...


class ControlWakeupSource(Protocol):
    """Deliver opaque reconciliation hints without exposing their payload to control."""

    async def receive(self) -> object: ...

    async def acknowledge(self, signal: object) -> None: ...


class CapacitySlot(Protocol):
    """One capacity reservation held for the complete executor lifetime."""

    def release(self) -> None: ...


class LeaseMonitor:
    """Renew the exact Attempt and fence local control when authority is lost."""

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
            # A Redis-compatible in-process backend may complete every await inline.
            # Keep teardown cancellable even when control has already terminalized.
            await sleep(0)
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
        adapter: Callable[[], HarnessOutcomeAdapter],
        committer: AttemptCommitter,
        capacity_slot: CapacitySlot,
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
        self._capacity_slot = capacity_slot

    async def run(self) -> AttemptOutcome | AttemptMutationReceipt | AttemptPreparationRejected:
        finalization: AttemptOutcome | AttemptMutationReceipt | AttemptPreparationRejected | None = None
        try:
            async with create_task_group() as tasks:
                self._control.bind_executor(self._driver, tasks.cancel_scope.cancel)
                try:
                    await tasks.start(LeaseMonitor(self._context, self._control).run)
                    await tasks.start(ControlWatcher(self._context, self._control, self._wakeups).run)
                    await self._preparer.claim_state_writer()
                    await self._preparer.validate_dependencies(self._control.current_context)
                    decision = await self._control.commit_preparation()
                    if isinstance(decision, AttemptPreparationRejected):
                        finalization = decision
                    else:
                        await self._control.reconcile_recovery_state()
                        if self._control.current_state.envelope.outcome_candidate is not None:
                            finalization = await self._control.recover_outcome(self._committer)
                        if finalization is None:
                            finalization = await self._execute(decision)
                except AttemptAuthorityError:
                    raise
                except Exception as error:
                    if self._control.handoff_ready:
                        raise
                    logger.warning(
                        "run_attempt_execution_failed",
                        extra={
                            "run_id": self._context.run_id,
                            "attempt_number": self._context.attempt_number,
                            "error_type": type(error).__name__,
                        },
                    )
                    if isinstance(error, SkillRuntimeError):
                        code = error.code
                    elif isinstance(
                        error, (ObjectStoreUnavailable, OSError, TimeoutError, StateClaimExhausted)
                    ) and not isinstance(error, PermissionError):
                        code = "attempt_dependency_unavailable"
                    else:
                        code = "attempt_execution_failed"
                    with fail_after(self._context.reconciliation_timeout.total_seconds()):
                        finalization = await self._control.fail_execution(
                            self._committer,
                            SafeFailure(code=code, message="The RunAttempt could not complete execution."),
                        )
                finally:
                    with CancelScope(shield=True):
                        await self._control.close_admission()
                        tasks.cancel_scope.cancel()
            if finalization is None:
                raise AttemptAuthorityError("Attempt executor stopped without an authoritative finalization")
            return finalization
        finally:
            self._capacity_slot.release()

    async def _execute(self, decision: AttemptPreparationAccepted) -> AttemptOutcome | AttemptMutationReceipt:
        async with self._preparer.open_runtime(self._control.current_context) as invocation:
            try:
                candidate = await self._driver.run(invocation, preparation=decision)
            finally:
                await self._control.close_delivery()
        return await self._control.finalize(candidate, adapter=self._adapter(), committer=self._committer)


__all__ = [
    "AttemptPreparer",
    "CapacitySlot",
    "ControlWakeupSource",
    "ControlWatcher",
    "LeaseMonitor",
    "RunAttemptExecutor",
]
