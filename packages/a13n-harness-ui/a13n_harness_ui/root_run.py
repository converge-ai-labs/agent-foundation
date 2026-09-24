"""Process-local receipt coordination for root Thread operations."""

from __future__ import annotations

import json
from collections import Counter, OrderedDict
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from time import monotonic
from typing import Any, Literal
from uuid import uuid4

from a13n_harness import HarnessRunStream, SafeFailure
from a13n_harness.input import RunInputValue
from a13n_logging import get_logger
from anyio import CancelScope, Event, Lock, create_task_group, get_cancelled_exc_class, move_on_after, to_thread
from anyio.abc import TaskGroup
from pydantic import JsonValue, TypeAdapter, ValidationError
from pydantic_ai.usage import RunUsage

from a13n_harness_ui.diagnostics import exception_feedback
from a13n_harness_ui.environment_bindings import EnvironmentSelectionPatch
from a13n_harness_ui.errors import HarnessUiError, RunCoordinationError
from a13n_harness_ui.goal import GoalView
from a13n_harness_ui.interaction_timeout import timeout_response
from a13n_harness_ui.live import HarnessUiSummaryHub, RootOperationNotice
from a13n_harness_ui.notifications import root_operation_notice
from a13n_harness_ui.observation import UiObservation, finish_operation, record_input, record_output
from a13n_harness_ui.restart import GracefulRestart
from a13n_harness_ui.restart_models import RestartItem
from a13n_harness_ui.root_execution import RootRunAdmission, RootRunExecutor, RootRunOutcome
from a13n_harness_ui.root_input import RootInputFiles, detach_input
from a13n_harness_ui.storage import ObjectRef, ThreadConfigurationMutation
from a13n_harness_ui.surfaces import (
    ContinuationSelectionView,
    EnvironmentOutcomeView,
    FailureView,
    RootActivityState,
    RootActivityView,
    RootControlResult,
    RootExecutionView,
    RootOperationStatus,
    RootOperationView,
    RootRunOutcomeView,
    RootRunReceipt,
    RunModelOverrides,
    ThreadDeferredResponse,
)

_MAX_WAIT_SECONDS = 60.0
_MAX_VALUE_BYTES = 64 * 1024
_JSON_ADAPTER = TypeAdapter(JsonValue)
_JSON_MAPPING_ADAPTER = TypeAdapter(dict[str, JsonValue])
_RUN_USAGE_ADAPTER = TypeAdapter(RunUsage)
_TERMINAL = frozenset(
    {
        RootOperationStatus.completed,
        RootOperationStatus.suspended,
        RootOperationStatus.failed,
        RootOperationStatus.cancelled,
    }
)


@dataclass(slots=True)
class _RootOperation:
    receipt: RootRunReceipt
    status: RootOperationStatus
    done: Event
    scope: CancelScope | None = None
    stream: HarnessRunStream[Any] | None = None
    input_files: RootInputFiles | None = None
    composition: ObjectRef | None = None
    run_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    outcome: RootRunOutcomeView | None = None
    failure: FailureView | None = None
    cancel_requested: bool = False
    restart: RestartItem | None = None
    goal: GoalView | None = None


@dataclass(slots=True)
class _InteractionWait:
    response: ThreadDeferredResponse
    expires_at: datetime
    deadline: float
    cancelled: Event


class RootRunCoordinator:
    """Own App-lifetime root tasks and correlate every control to one receipt."""

    def __init__(
        self,
        executor: RootRunExecutor,
        *,
        summary_hub: HarnessUiSummaryHub | None = None,
        terminal_retention: int = 256,
        observation: UiObservation | None = None,
        touch_thread: Callable[[str], Awaitable[None]] | None = None,
        interaction_timeouts: bool = False,
        notify: Callable[[str, RootOperationNotice], None] | None = None,
        on_settled: Callable[[str | None, RootOperationView], Awaitable[None]] | None = None,
        on_human_admitted: Callable[[RootRunAdmission, RootOperationView], Awaitable[None]] | None = None,
        restart_coordinator: GracefulRestart | None = None,
    ) -> None:
        if terminal_retention < 1:
            raise ValueError("terminal_retention must be positive")
        self._observation = observation or UiObservation()
        self._restart = restart_coordinator
        self._executor = executor
        self._touch_thread = touch_thread
        self._summary_hub = summary_hub
        self._notify = notify
        self._on_settled = on_settled
        self._on_human_admitted = on_human_admitted
        self._lock = Lock()
        self._operations: dict[str, _RootOperation] = {}
        self._active_by_thread: dict[str, str] = {}
        self._latest_terminal: OrderedDict[str, RootOperationView] = OrderedDict()
        self._terminal_receipts: OrderedDict[str, None] = OrderedDict()
        self._terminal_retention = terminal_retention
        self._task_group_context: Any | None = None
        self._task_group: TaskGroup | None = None
        self._accepting = False
        self._interaction_timeouts = interaction_timeouts
        self._interaction_waits: dict[str, _InteractionWait] = {}

    async def start(self) -> None:
        async with self._lock:
            if self._task_group is not None:
                raise RunCoordinationError("Root coordination is already started.", code="root_coordinator_started")
            context = create_task_group()
            task_group = await context.__aenter__()
            self._task_group_context = context
            self._task_group = task_group
            self._accepting = True

    async def stop_admission(self) -> None:
        async with self._lock:
            self._accepting = False
            self._cancel_interactions()

    def _cancel_interactions(self) -> None:
        for pending in self._interaction_waits.values():
            pending.cancelled.set()
        self._interaction_waits.clear()

    async def interaction_expiry(self, thread_id: str, continuation_id: str) -> datetime | None:
        """Return only the current process's deadline for this exact continuation."""
        async with self._lock:
            pending = self._interaction_waits.get(thread_id)
            if pending is None or pending.response.expected_continuation_id != continuation_id:
                return None
            return pending.expires_at

    async def active_thread_ids(self) -> tuple[str, ...]:
        """Snapshot all preparing, running and cancelling roots in this App."""

        async with self._lock:
            return tuple(self._active_by_thread)

    async def active_count(self) -> int:
        """Return the number of process-local active root operations."""

        async with self._lock:
            return len(self._active_by_thread)

    async def close(self, *, timeout_seconds: float) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        context = self._task_group_context
        task_group = self._task_group
        if task_group is not None:
            task_group.cancel_scope.shield = True
        active: tuple[_RootOperation, ...] = ()
        # The group's own failure cancellation is not blocked by its shield.
        # Leave this inner scope before exiting the manually entered task group.
        with CancelScope(shield=True):
            async with self._lock:
                self._accepting = False
                self._cancel_interactions()
                active = tuple(
                    self._operations[receipt_id]
                    for receipt_id in self._active_by_thread.values()
                    if receipt_id in self._operations
                )
            for operation in active:
                await self._request_cancel(operation)
        if context is None or task_group is None:
            return
        with move_on_after(timeout_seconds, shield=True) as grace:
            for operation in active:
                await operation.done.wait()
        if grace.cancel_called:
            task_group.cancel_scope.cancel()
        try:
            # Structured ownership requires the join before the App closes its store.
            # The timeout bounds graceful drain before cooperative cancellation; it
            # cannot forcibly preempt trusted Python code that shields cancellation.
            await context.__aexit__(None, None, None)
        finally:
            with CancelScope(shield=True):
                async with self._lock:
                    self._task_group_context = None
                    self._task_group = None
                    self._active_by_thread.clear()

    @asynccontextmanager
    async def require_inactive(self, thread_id: str) -> AsyncGenerator[None]:
        """Serialize an idle-only mutation against root admission for one Thread."""

        async with self._lock:
            if thread_id in self._active_by_thread:
                raise RunCoordinationError(
                    "Wait for the active root operation to finish before changing this Thread.",
                    code="thread_run_active",
                )
            yield
            pending = self._interaction_waits.pop(thread_id, None)
            if pending is not None:
                pending.cancelled.set()

    async def submit_prompt(
        self,
        *,
        thread_id: str,
        prompt: RunInputValue,
        environment: EnvironmentSelectionPatch | None = None,
        mutation: ThreadConfigurationMutation | None = None,
        model_overrides: RunModelOverrides | None = None,
        touch: bool = False,
        goal: GoalView | None = None,
        human_input: bool = False,
    ) -> RootRunReceipt:
        prompt = detach_input(prompt)
        return await self._submit(
            thread_id=thread_id,
            prompt=prompt,
            response=None,
            environment=environment,
            mutation=mutation,
            model_overrides=model_overrides,
            touch=touch,
            goal=goal,
            human_input=human_input,
        )

    async def submit_response(
        self,
        *,
        thread_id: str,
        response: ThreadDeferredResponse,
        mutation: ThreadConfigurationMutation | None = None,
        model_overrides: RunModelOverrides | None = None,
        touch: bool = False,
    ) -> RootRunReceipt:
        return await self._submit(
            thread_id=thread_id,
            prompt=None,
            response=response.model_copy(deep=True),
            mutation=mutation,
            model_overrides=model_overrides,
            touch=touch,
        )

    async def resume_restart(self, item: RestartItem) -> RootRunReceipt:
        return await self._submit(
            thread_id=item.thread_id,
            prompt=None,
            response=None,
            mutation=None,
            model_overrides=None,
            touch=False,
            restart=item,
        )

    async def _submit(
        self,
        *,
        thread_id: str,
        prompt: RunInputValue | None,
        response: ThreadDeferredResponse | None,
        mutation: ThreadConfigurationMutation | None,
        model_overrides: RunModelOverrides | None,
        touch: bool,
        timeout: _InteractionWait | None = None,
        restart: RestartItem | None = None,
        environment: EnvironmentSelectionPatch | None = None,
        goal: GoalView | None = None,
        human_input: bool = False,
    ) -> RootRunReceipt:
        now = datetime.now(UTC)
        receipt = RootRunReceipt(
            receipt_id=f"receipt-{uuid4().hex}",
            thread_id=thread_id,
            submitted_at=now,
        )
        operation = _RootOperation(
            receipt=receipt,
            status=RootOperationStatus.preparing,
            done=Event(),
            restart=restart,
            goal=goal,
        )
        async with self._lock:
            if self._restart is not None:
                if restart is None:
                    self._restart.require_input()
            if not self._accepting or self._task_group is None:
                raise RunCoordinationError("Root coordination is not accepting work.", code="app_stopping")
            if thread_id in self._active_by_thread:
                raise RunCoordinationError(
                    "This Thread already has an active root operation.",
                    code="thread_run_active",
                )
            pending = self._interaction_waits.get(thread_id)
            if timeout is not None and pending is not timeout:
                raise RunCoordinationError("The interaction is no longer pending.", code="thread_deferred_not_pending")
            matching = (
                pending is not None
                and response is not None
                and pending.response.expected_continuation_id == response.expected_continuation_id
            )
            if pending is not None and matching and timeout is None and monotonic() >= pending.deadline:
                raise RunCoordinationError("The interaction deadline has elapsed.", code="thread_interaction_expired")
            with CancelScope(shield=True):
                if touch and self._touch_thread is not None:
                    try:
                        await self._touch_thread(thread_id)
                    except HarnessUiError:
                        raise
                    except Exception as exc:
                        # Preserve typed admission failures for callers that have
                        # already created a durable Thread and must return its ID.
                        raise RunCoordinationError(
                            "Could not update Thread navigation recency; work was not admitted.",
                            code="thread_touch_failed",
                        ) from exc
                admission = await self._executor.capture(
                    thread_id=thread_id,
                    prompt=prompt,
                    response=response,
                    restart=restart,
                    mutation=mutation,
                    model_overrides=None if model_overrides is None else model_overrides.model_copy(deep=True),
                    environment=environment,
                )
                operation.composition = admission.published.reference
                if matching and pending is not None:
                    self._interaction_waits.pop(thread_id)
                    pending.cancelled.set()
                if self._restart is not None:
                    self._restart.register(thread_id)
                self._operations[receipt.receipt_id] = operation
                self._active_by_thread[thread_id] = receipt.receipt_id
                self._task_group.start_soon(self._run_operation, operation, admission)
                if human_input and self._on_human_admitted is not None:
                    self._task_group.start_soon(self._notify_human_admitted, admission, _view(operation))
        await self._publish_change(operation)
        return receipt.model_copy(deep=True)

    async def _notify_human_admitted(self, admission: RootRunAdmission, operation: RootOperationView) -> None:
        if self._on_human_admitted is None or not self._accepting:
            return
        try:
            await self._on_human_admitted(admission, operation)
        except Exception:
            # Admission already succeeded. Notification cannot reject or replay it.
            get_logger(__name__).warning("Could not notify Coordinator of human admission", exc_info=True)

    async def get(self, receipt_id: str) -> RootOperationView:
        async with self._lock:
            operation = self._operations.get(receipt_id)
            if operation is None:
                raise RunCoordinationError("Root receipt does not exist in this App.", code="root_receipt_missing")
            return _view(operation)

    async def active(self, thread_id: str) -> RootOperationView | None:
        async with self._lock:
            receipt_id = self._active_by_thread.get(thread_id)
            if receipt_id is None:
                return None
            operation = self._operations.get(receipt_id)
            return None if operation is None else _view(operation)

    async def activity(self, thread_id: str) -> RootActivityView:
        return (await self.activities((thread_id,)))[thread_id]

    async def activities(self, thread_ids: tuple[str, ...]) -> dict[str, RootActivityView]:
        async with self._lock:
            result: dict[str, RootActivityView] = {}
            for thread_id in thread_ids:
                receipt_id = self._active_by_thread.get(thread_id)
                operation = None if receipt_id is None else self._operations.get(receipt_id)
                if operation is None:
                    result[thread_id] = RootActivityView(state=RootActivityState.inactive)
                elif operation.status is RootOperationStatus.preparing:
                    result[thread_id] = RootActivityView(
                        state=RootActivityState.preparing,
                        receipt_id=operation.receipt.receipt_id,
                        available_actions=("wait", "cancel"),
                    )
                else:
                    result[thread_id] = RootActivityView(
                        state=RootActivityState.running,
                        receipt_id=operation.receipt.receipt_id,
                        run_id=operation.run_id,
                        available_actions=("wait", "steer", "cancel"),
                    )
            return result

    async def latest(self, thread_id: str) -> RootOperationView | None:
        async with self._lock:
            value = self._latest_terminal.get(thread_id)
            return None if value is None else value.model_copy(deep=True)

    async def latest_many(self, thread_ids: tuple[str, ...]) -> dict[str, RootOperationView]:
        async with self._lock:
            return {
                thread_id: value.model_copy(deep=True)
                for thread_id in thread_ids
                if (value := self._latest_terminal.get(thread_id)) is not None
            }

    async def wait(
        self,
        receipt_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> RootOperationView:
        if timeout_seconds is not None and not 0 <= timeout_seconds <= _MAX_WAIT_SECONDS:
            raise RunCoordinationError("Root wait timeout is outside supported bounds.", code="root_wait_invalid")
        async with self._lock:
            operation = self._operations.get(receipt_id)
            if operation is None:
                raise RunCoordinationError("Root receipt does not exist in this App.", code="root_receipt_missing")
            done = operation.done
        if timeout_seconds is None:
            await done.wait()
        elif timeout_seconds > 0:
            with move_on_after(timeout_seconds):
                await done.wait()
        return await self.get(receipt_id)

    async def steer(self, *, receipt_id: str, message: RunInputValue, touch: bool = False) -> RootControlResult:
        message = detach_input(message)
        async with self._lock:
            operation = self._operations.get(receipt_id)
            if operation is None:
                raise RunCoordinationError("Root receipt does not exist in this App.", code="root_receipt_missing")
            stream = operation.stream if operation.status is RootOperationStatus.running else None
            input_files = operation.input_files
        if stream is None:
            return RootControlResult(receipt_id=receipt_id, accepted=False)
        try:
            prepared = (
                await input_files.prepare(message, stream.context.environment) if input_files is not None else message
            )
            if self._restart is not None:
                self._restart.require_input()
            enqueue_id = await stream.steer(prepared)
        except Exception:
            return RootControlResult(receipt_id=receipt_id, accepted=False)
        if touch and self._touch_thread is not None:
            # Input is already enqueued: a recency failure must not report rejection
            # and invite the caller to submit the same steering twice.
            with CancelScope(shield=True):
                try:
                    await self._touch_thread(operation.receipt.thread_id)
                except Exception:
                    get_logger(__name__).warning(
                        "Could not update Thread recency after accepted steering", exc_info=True
                    )
            await self._publish_change(operation)
        return RootControlResult(receipt_id=receipt_id, accepted=True, enqueue_id=enqueue_id)

    async def cancel(self, receipt_id: str) -> RootControlResult:
        async with self._lock:
            operation = self._operations.get(receipt_id)
            if operation is None:
                raise RunCoordinationError("Root receipt does not exist in this App.", code="root_receipt_missing")
            if operation.status in _TERMINAL:
                return RootControlResult(receipt_id=receipt_id, accepted=False)
            operation.cancel_requested = True
            if self._restart is not None:
                self._restart.active.pop(operation.receipt.thread_id, None)
                self._restart.signal()
        await self._request_cancel(operation)
        return RootControlResult(receipt_id=receipt_id, accepted=True)

    async def _goal_changed(self, operation: _RootOperation, goal: GoalView) -> None:
        async with self._lock:
            operation.goal = goal
        await self._publish_change(operation)

    async def _request_cancel(self, operation: _RootOperation) -> None:
        async with self._lock:
            operation.cancel_requested = True
            scope = operation.scope
            stream = operation.stream
        if stream is not None:
            stream.cancel()
        if scope is not None:
            scope.cancel()

    async def _run_operation(
        self,
        operation: _RootOperation,
        admission: RootRunAdmission,
    ) -> None:
        with self._observation.operation(
            "root", thread_id=operation.receipt.thread_id, operation_id=operation.receipt.receipt_id
        ) as span:
            record_input(
                admission.prompt if admission.response is None else admission.response,
                kind="prompt" if admission.response is None else "deferred_response",
            )
            await self._execute_operation(operation, admission)
            record_output(
                operation.outcome.execution.output if operation.outcome is not None else None,
                status=operation.status.value,
            )
            finish_operation(
                span,
                status=operation.status.value,
                run_id=operation.run_id,
                error_code=operation.failure.code if operation.failure is not None else None,
            )

    async def _execute_operation(
        self,
        operation: _RootOperation,
        admission: RootRunAdmission,
    ) -> None:
        scope = CancelScope()
        async with self._lock:
            operation.scope = scope
            cancel_requested = operation.cancel_requested
        outcome: RootRunOutcome | None = None
        failure: FailureView | None = None
        cancelled = False
        cancelled_class = get_cancelled_exc_class()
        try:
            with scope:
                if cancel_requested:
                    scope.cancel()
                else:
                    outcome = await self._executor.execute(
                        admission,
                        goal=operation.goal,
                        on_goal=lambda value: self._goal_changed(operation, value),
                        on_stream=lambda stream, input_files=None: self._running(
                            operation.receipt.receipt_id, stream, input_files
                        ),
                    )
            if outcome is None:
                cancelled = True
        except cancelled_class:
            cancelled = True
        except BaseException as exc:
            if isinstance(exc, HarnessUiError):
                failure = _exception_failure(exc, code="root_operation_failed")
            else:
                with CancelScope(shield=True):
                    feedback = await to_thread.run_sync(
                        partial(
                            exception_feedback,
                            exc,
                            thread_id=operation.receipt.thread_id,
                            run_id=operation.run_id,
                            phase="root_operation",
                        )
                    )
                    failure = FailureView(
                        code="root_operation_failed", message=f"Unexpected {type(exc).__name__}.\n{feedback}"
                    )
        with CancelScope(shield=True):
            # Completion is navigation-worthy, unlike streamed progress. Persist it
            # before releasing waiters/publishing the terminal view, outside the lock.
            if self._touch_thread is not None:
                try:
                    await self._touch_thread(operation.receipt.thread_id)
                except Exception:
                    get_logger(__name__).exception("Could not touch completed Thread: %s", operation.receipt.thread_id)
            async with self._lock:
                if cancelled:
                    operation.status = RootOperationStatus.cancelled
                elif failure is not None:
                    operation.status = RootOperationStatus.failed
                    operation.failure = failure
                else:
                    assert outcome is not None
                    projected = _outcome(outcome)
                    operation.outcome = projected
                    operation.status = _terminal_status(outcome, projected)
                if (
                    operation.goal is not None
                    and operation.goal.active
                    and operation.status
                    in {
                        RootOperationStatus.failed,
                        RootOperationStatus.cancelled,
                    }
                ):
                    operation.goal = operation.goal.model_copy(
                        update={
                            "status": "cancelled" if operation.status is RootOperationStatus.cancelled else "error",
                        }
                    )
                operation.completed_at = datetime.now(UTC)
                operation.scope = None
                operation.stream = None
                if self._active_by_thread.get(operation.receipt.thread_id) == operation.receipt.receipt_id:
                    self._active_by_thread.pop(operation.receipt.thread_id, None)
                thread_id = operation.receipt.thread_id
                self._latest_terminal.pop(thread_id, None)
                self._latest_terminal[thread_id] = _view(operation)
                while len(self._latest_terminal) > self._terminal_retention:
                    self._latest_terminal.popitem(last=False)
                receipt_id = operation.receipt.receipt_id
                self._terminal_receipts[receipt_id] = None
                while len(self._terminal_receipts) > self._terminal_retention:
                    expired_receipt, _ = self._terminal_receipts.popitem(last=False)
                    self._operations.pop(expired_receipt, None)
                if outcome is not None and outcome.continuation.status == "selected":
                    pending = self._interaction_waits.get(thread_id)
                    reference = outcome.continuation.reference
                    if (
                        pending is not None
                        and reference is not None
                        and (pending.response.expected_continuation_id != reference.logical_digest)
                    ):
                        self._interaction_waits.pop(thread_id)
                        pending.cancelled.set()
                if operation.status is RootOperationStatus.suspended and outcome is not None:
                    self._start_interaction(operation, outcome)
                if self._restart is not None:
                    self._restart.finished(operation.receipt.thread_id, failed=failure is not None)
                operation.done.set()
            # Notify only after the Host has settled execution and continuation selection.
            # Projection and delivery are best effort, never part of execution success.
            try:
                notice = root_operation_notice(
                    _view(operation),
                    output=outcome.result.output
                    if outcome is not None and isinstance(outcome.result.output, str)
                    else None,
                    deferred=outcome.result.deferred if outcome is not None else None,
                )
            except Exception:
                notice = None
            await self._publish_change(operation, notice=notice)
            if self._on_settled is not None and self._accepting:
                try:
                    await self._on_settled(admission.published.value.project_id, _view(operation))
                except Exception:
                    get_logger(__name__).warning(
                        "Could not deliver root operation notification: %s", operation.receipt.receipt_id, exc_info=True
                    )

    def _start_interaction(self, operation: _RootOperation, outcome: RootRunOutcome) -> None:
        """Arm once, under the admission lock, after successful continuation selection."""
        if self._restart is not None and (self._restart.requested or self._restart.restoring):
            return
        if not self._interaction_timeouts or not self._accepting or self._task_group is None:
            return
        reference, requests = outcome.continuation.reference, outcome.result.deferred
        if reference is None or requests is None or not (requests.calls or requests.approvals):
            return
        thread_id = operation.receipt.thread_id
        previous = self._interaction_waits.pop(thread_id, None)
        if previous is not None:
            previous.cancelled.set()
        try:
            response = timeout_response(reference.logical_digest, requests)
        except ValueError:
            get_logger(__name__).warning("Interaction timeout unavailable: thread_id=%s", thread_id)
            return
        seconds = outcome.interaction_timeout_seconds
        pending = _InteractionWait(
            response=response,
            expires_at=datetime.now(UTC) + timedelta(seconds=seconds),
            deadline=monotonic() + seconds,
            cancelled=Event(),
        )
        self._interaction_waits[thread_id] = pending
        self._task_group.start_soon(self._expire_interaction, thread_id, pending)

    async def _expire_interaction(self, thread_id: str, pending: _InteractionWait) -> None:
        with move_on_after(max(0, pending.deadline - monotonic())):
            await pending.cancelled.wait()
        while not pending.cancelled.is_set():
            # An unrelated admission can still be preparing (and may fail). Wait
            # for it without retrying an admitted timeout response or holding a lock.
            async with self._lock:
                if not self._accepting or self._interaction_waits.get(thread_id) is not pending:
                    return
                active_id = self._active_by_thread.get(thread_id)
                active = self._operations.get(active_id) if active_id is not None else None
            if active is not None:
                await active.done.wait()
                continue
            try:
                await self._submit(
                    thread_id=thread_id,
                    prompt=None,
                    response=pending.response,
                    mutation=None,
                    model_overrides=None,
                    touch=False,
                    timeout=pending,
                )
            except RunCoordinationError as exc:
                if exc.code == "thread_run_active":
                    continue
                return
            return

    async def composition_reference(self, receipt_id: str) -> ObjectRef | None:
        async with self._lock:
            operation = self._operations.get(receipt_id)
            if operation is None:
                raise RunCoordinationError("Root receipt does not exist in this App.", code="root_receipt_missing")
            return operation.composition

    async def _running(
        self, receipt_id: str, stream: HarnessRunStream[Any], input_files: RootInputFiles | None = None
    ) -> None:
        async with self._lock:
            operation = self._operations.get(receipt_id)
            if operation is None or operation.status is not RootOperationStatus.preparing:
                raise RunCoordinationError(
                    "The root receipt is no longer current.",
                    code="thread_run_admission_invalid",
                )
            operation.stream = stream
            operation.input_files = input_files
            operation.run_id = stream.run_id
            operation.started_at = datetime.now(UTC)
            operation.status = RootOperationStatus.running
            cancel_requested = operation.cancel_requested
        await self._publish_change(operation)
        if cancel_requested:
            stream.cancel()

    async def _publish_change(self, operation: _RootOperation, *, notice: RootOperationNotice | None = None) -> None:
        if notice is not None and self._notify is not None:
            try:
                self._notify(operation.receipt.thread_id, notice)
            except Exception:
                pass  # Optional delivery cannot change execution settlement.
        if self._summary_hub is None:
            return
        try:
            await self._summary_hub.publish(
                kind="root_operation",
                root_thread_id=operation.receipt.thread_id,
                thread_id=operation.receipt.thread_id,
                notice=notice,
            )
            if operation.status is not RootOperationStatus.preparing:
                await self._summary_hub.publish(
                    kind="thread",
                    root_thread_id=operation.receipt.thread_id,
                    thread_id=operation.receipt.thread_id,
                )
        except Exception:
            return


def _view(operation: _RootOperation) -> RootOperationView:
    if operation.status is RootOperationStatus.preparing:
        actions: tuple[Literal["wait", "steer", "cancel"], ...] = ("wait", "cancel")
    elif operation.status is RootOperationStatus.running:
        actions = ("wait", "steer", "cancel")
    else:
        actions = ()
    return RootOperationView(
        receipt=operation.receipt,
        status=operation.status,
        run_id=operation.run_id,
        started_at=operation.started_at,
        completed_at=operation.completed_at,
        outcome=operation.outcome,
        failure=operation.failure,
        goal=operation.goal,
        available_actions=actions,
    ).model_copy(deep=True)


def _terminal_status(outcome: RootRunOutcome, projected: RootRunOutcomeView) -> RootOperationStatus:
    status = outcome.result.status
    if status == "cancelled":
        return RootOperationStatus.cancelled
    if status == "failed" or projected.continuation.status == "failed":
        return RootOperationStatus.failed
    if status == "suspended" and projected.continuation.status == "selected":
        return RootOperationStatus.suspended
    if status == "completed" and projected.continuation.status == "selected":
        return RootOperationStatus.completed
    return RootOperationStatus.failed


def _outcome(value: RootRunOutcome) -> RootRunOutcomeView:
    output, output_omitted = _bounded_json(value.result.output)
    usage = _json_mapping(_RUN_USAGE_ADAPTER.dump_python(value.result.usage, mode="json"))
    continuation_failure = (
        None
        if value.continuation.error is None
        else _exception_failure(value.continuation.error, code="continuation_selection_failed")
    )
    publications = Counter(item.status for item in value.environment.state_publications)
    cleanup_failures = tuple(
        _exception_failure(item, code="environment_cleanup_failed") for item in value.environment.cleanup_errors
    )
    return RootRunOutcomeView(
        execution=RootExecutionView(
            status=value.result.status,
            output=output,
            output_omitted=output_omitted,
            failure=None if value.result.failure is None else _safe_failure(value.result.failure),
            usage=usage,
        ),
        continuation=ContinuationSelectionView(
            status=value.continuation.status,
            continuation_id=(
                None if value.continuation.reference is None else value.continuation.reference.logical_digest
            ),
            failure=continuation_failure,
        ),
        environment=EnvironmentOutcomeView(
            unchanged=publications["unchanged"],
            published=publications["published"],
            failed=publications["failed"],
            cleanup_failures=cleanup_failures,
        ),
        composition_id=value.composition.logical_digest,
    )


def _safe_failure(value: SafeFailure) -> FailureView:
    return FailureView(
        code=value.code[:256],
        message=_bounded_message(value.message),
        details=_json_mapping(value.details),
        retry_hint=value.retry_hint,
    )


def _exception_failure(value: BaseException, *, code: str) -> FailureView:
    if isinstance(value, HarnessUiError):
        return FailureView(
            code=value.code[:256],
            message=_bounded_message(str(value)),
            details=_json_mapping(value.details),
        )
    return FailureView(code=code, message=_bounded_message(str(value) or type(value).__name__))


def _bounded_message(value: str) -> str:
    if len(value) <= 32 * 1024:
        return value
    return value[: 32 * 1024 - 23] + "\n...[message truncated]"


def _bounded_json(value: object) -> tuple[JsonValue | None, bool]:
    try:
        projected = _JSON_ADAPTER.validate_python(value)
        encoded = json.dumps(projected, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, ValidationError):
        return None, True
    if len(encoded) > _MAX_VALUE_BYTES:
        return None, True
    return projected, False


def _json_mapping(value: object) -> dict[str, JsonValue] | None:
    try:
        projected = _JSON_MAPPING_ADAPTER.validate_python(value)
        encoded = json.dumps(projected, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, ValidationError):
        return None
    if len(encoded) > _MAX_VALUE_BYTES:
        return None
    return projected


__all__ = ["RootRunCoordinator"]
