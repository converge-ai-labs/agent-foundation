"""Process-local receipt coordination for root Thread operations."""

from __future__ import annotations

import json
from collections import Counter, OrderedDict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any, Literal
from uuid import uuid4

from a13n_harness import HarnessRunStream, SafeFailure
from a13n_harness.input import RunInputValue
from anyio import CancelScope, Event, Lock, create_task_group, get_cancelled_exc_class, move_on_after, to_thread
from anyio.abc import TaskGroup
from pydantic import JsonValue, TypeAdapter, ValidationError
from pydantic_ai.usage import RunUsage

from a13n_harness_ui.diagnostics import exception_feedback
from a13n_harness_ui.errors import HarnessUiError, RunCoordinationError
from a13n_harness_ui.live import HarnessUiSummaryHub
from a13n_harness_ui.root_execution import RootRunExecutor, RootRunOutcome
from a13n_harness_ui.root_input import detach_input
from a13n_harness_ui.storage import ThreadConfigurationMutation
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
    run_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    outcome: RootRunOutcomeView | None = None
    failure: FailureView | None = None
    cancel_requested: bool = False


class RootRunCoordinator:
    """Own App-lifetime root tasks and correlate every control to one receipt."""

    def __init__(
        self,
        executor: RootRunExecutor,
        *,
        summary_hub: HarnessUiSummaryHub | None = None,
        terminal_retention: int = 256,
    ) -> None:
        if terminal_retention < 1:
            raise ValueError("terminal_retention must be positive")
        self._executor = executor
        self._summary_hub = summary_hub
        self._lock = Lock()
        self._operations: dict[str, _RootOperation] = {}
        self._active_by_thread: dict[str, str] = {}
        self._latest_terminal: OrderedDict[str, RootOperationView] = OrderedDict()
        self._terminal_receipts: OrderedDict[str, None] = OrderedDict()
        self._terminal_retention = terminal_retention
        self._task_group_context: Any | None = None
        self._task_group: TaskGroup | None = None
        self._accepting = False

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

    async def active_count(self) -> int:
        """Return the number of process-local active root operations."""

        async with self._lock:
            return len(self._active_by_thread)

    async def close(self, *, timeout_seconds: float) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        owned_task_group = self._task_group
        if owned_task_group is not None:
            owned_task_group.cancel_scope.shield = True
        async with self._lock:
            self._accepting = False
            active = tuple(
                self._operations[receipt_id]
                for receipt_id in self._active_by_thread.values()
                if receipt_id in self._operations
            )
            context = self._task_group_context
            task_group = self._task_group
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
        """Serialize an archive transition against root admission for one Thread."""

        async with self._lock:
            if thread_id in self._active_by_thread:
                raise RunCoordinationError(
                    "An active root Thread cannot be archived.",
                    code="thread_run_active",
                )
            yield

    async def submit_prompt(
        self,
        *,
        thread_id: str,
        prompt: RunInputValue,
        mutation: ThreadConfigurationMutation | None = None,
        model_overrides: RunModelOverrides | None = None,
    ) -> RootRunReceipt:
        prompt = detach_input(prompt)
        return await self._submit(
            thread_id=thread_id,
            prompt=prompt,
            response=None,
            mutation=mutation,
            model_overrides=model_overrides,
        )

    async def submit_response(
        self,
        *,
        thread_id: str,
        response: ThreadDeferredResponse,
        mutation: ThreadConfigurationMutation | None = None,
        model_overrides: RunModelOverrides | None = None,
    ) -> RootRunReceipt:
        return await self._submit(
            thread_id=thread_id,
            prompt=None,
            response=response.model_copy(deep=True),
            mutation=mutation,
            model_overrides=model_overrides,
        )

    async def _submit(
        self,
        *,
        thread_id: str,
        prompt: RunInputValue | None,
        response: ThreadDeferredResponse | None,
        mutation: ThreadConfigurationMutation | None,
        model_overrides: RunModelOverrides | None,
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
        )
        async with self._lock:
            if not self._accepting or self._task_group is None:
                raise RunCoordinationError("Root coordination is not accepting work.", code="app_stopping")
            if thread_id in self._active_by_thread:
                raise RunCoordinationError(
                    "This Thread already has an active root operation.",
                    code="thread_run_active",
                )
            self._operations[receipt.receipt_id] = operation
            self._active_by_thread[thread_id] = receipt.receipt_id
            self._task_group.start_soon(
                self._run_operation,
                operation,
                prompt,
                response,
                mutation,
                None if model_overrides is None else model_overrides.model_copy(deep=True),
            )
        await self._publish_change(operation)
        return receipt.model_copy(deep=True)

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

    async def steer(self, *, receipt_id: str, message: str) -> RootControlResult:
        if not message.strip():
            raise RunCoordinationError("A steering message must not be blank.", code="run_input_invalid")
        async with self._lock:
            operation = self._operations.get(receipt_id)
            if operation is None:
                raise RunCoordinationError("Root receipt does not exist in this App.", code="root_receipt_missing")
            stream = operation.stream if operation.status is RootOperationStatus.running else None
        if stream is None:
            return RootControlResult(receipt_id=receipt_id, accepted=False)
        try:
            enqueue_id = await stream.steer(message)
        except Exception:
            return RootControlResult(receipt_id=receipt_id, accepted=False)
        return RootControlResult(receipt_id=receipt_id, accepted=True, enqueue_id=enqueue_id)

    async def cancel(self, receipt_id: str) -> RootControlResult:
        async with self._lock:
            operation = self._operations.get(receipt_id)
            if operation is None:
                raise RunCoordinationError("Root receipt does not exist in this App.", code="root_receipt_missing")
            if operation.status in _TERMINAL:
                return RootControlResult(receipt_id=receipt_id, accepted=False)
            operation.cancel_requested = True
        await self._request_cancel(operation)
        return RootControlResult(receipt_id=receipt_id, accepted=True)

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
        prompt: RunInputValue | None,
        response: ThreadDeferredResponse | None,
        mutation: ThreadConfigurationMutation | None,
        model_overrides: RunModelOverrides | None,
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
                        thread_id=operation.receipt.thread_id,
                        prompt=prompt,
                        response=response,
                        mutation=mutation,
                        model_overrides=model_overrides,
                        on_stream=lambda stream: self._running(operation.receipt.receipt_id, stream),
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
                operation.done.set()
            await self._publish_change(operation)

    async def _running(self, receipt_id: str, stream: HarnessRunStream[Any]) -> None:
        async with self._lock:
            operation = self._operations.get(receipt_id)
            if operation is None or operation.status is not RootOperationStatus.preparing:
                raise RunCoordinationError(
                    "The root receipt is no longer current.",
                    code="thread_run_admission_invalid",
                )
            operation.stream = stream
            operation.run_id = stream.run_id
            operation.started_at = datetime.now(UTC)
            operation.status = RootOperationStatus.running
            cancel_requested = operation.cancel_requested
        await self._publish_change(operation)
        if cancel_requested:
            stream.cancel()

    async def _publish_change(self, operation: _RootOperation) -> None:
        if self._summary_hub is None:
            return
        try:
            await self._summary_hub.publish(
                kind="root_operation",
                root_thread_id=operation.receipt.thread_id,
                thread_id=operation.receipt.thread_id,
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
