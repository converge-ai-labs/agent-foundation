"""Host-held, process-local ownership of admitted tool invocations.

Stores contain execution facts and results, never executable continuations. A Host
may replace the memory store without replacing the execution runtime.
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import uuid4

from a13n_logging import get_logger
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic_ai.exceptions import ModelRetry, ToolFailed

from a13n_harness._retained_scope import RetainedScope as RetainedScope
from a13n_harness.environment.process_recovery import ShellExecutionState
from a13n_harness.tools.diagnostics import system_error, system_failure, tool_error_text

_OWNED_TOOL_EXECUTION: contextvars.ContextVar[bool] = contextvars.ContextVar("a13n_owned_tool_execution", default=False)


class ToolExecutionConfig(BaseModel):
    """Captured conversation wait policy, independent of execution deadlines."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["inline", "auto"] = "inline"
    foreground_wait_seconds: float = Field(default=5.0, ge=0, allow_inf_nan=False)


class ToolExecutionRecord(BaseModel):
    """An observation of one original call, scoped to its owning Thread."""

    model_config = ConfigDict(frozen=True)

    execution_id: str
    thread_id: str
    run_id: str
    tool_call_id: str
    tool_name: str
    # Original explicit Thread-wide completion ordering, not the native per-tool batch flag.
    sequential: bool = False
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "lost"] = "queued"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    result_available: bool = False
    foreground_released: bool = False
    delivery_acknowledged: bool = False
    cancel_requested: bool = False
    error: str | None = None

    @property
    def terminal(self) -> bool:
        return self.status not in {"queued", "running"}

    def receipt(self) -> dict[str, str]:
        """Close a foreground call without delivering or reissuing its owned invocation."""
        return {
            "type": "tool_execution_receipt",
            "execution_id": self.execution_id,
            "tool_name": self.tool_name,
            "status": self.status,
            "message": "The original invocation is owned by the runtime. Use tool_execution_result or tool_execution_wait; do not reissue it.",
        }


@dataclass(frozen=True)
class ToolExecutionArtifact:
    """Retained spill bytes, independent of the originating Environment lifetime."""

    path: str
    data: bytes
    suffix: str


_TOOL_EXECUTION_ARTIFACTS: contextvars.ContextVar[list[ToolExecutionArtifact] | None] = contextvars.ContextVar(
    "a13n_tool_execution_artifacts", default=None
)


@dataclass(frozen=True)
class StoredToolExecution:
    """Record and native result saved together at the completion boundary."""

    record: ToolExecutionRecord
    result: Any = None
    artifacts: tuple[ToolExecutionArtifact, ...] = ()
    retry_prompt: str | None = None
    shell: ShellExecutionState | None = None


class ToolExecutionLost(ToolFailed):
    """The original native execution can no longer be observed safely."""


class ToolExecutionSuspended(Exception):
    """Native supervision needs another Host claim; the invocation is not terminal."""


class ToolExecutionAdmissionRejected(ToolFailed):
    """The initial save was definitively rejected without creating a durable identity.

    Stores must not use this for an uncertain commit or an already admitted call.
    """


class ToolExecutionStore(Protocol):
    """Storage port; implementations serialize native results and own retention.

    All lookups enforce Thread scope. ``save`` publishes a record and its result
    atomically. Acknowledgment changes delivery only, never execution status.
    """

    async def save(self, execution: StoredToolExecution) -> None: ...

    async def get(self, thread_id: str, execution_id: str) -> StoredToolExecution | None: ...

    async def list(self, thread_id: str, *, offset: int, limit: int) -> tuple[ToolExecutionRecord, ...]:
        """Page active, undelivered, failed, then delivered records; newest first in each group."""
        ...

    async def release_foreground(self, thread_id: str, execution_id: str) -> None:
        """Atomically mark receipt delivery without changing execution or result facts."""
        ...

    async def acknowledge_delivery(self, thread_id: str, execution_id: str) -> None: ...

    async def request_cancellation(self, thread_id: str, execution_id: str) -> None:
        """Persist cancellation intent without replacing execution or result facts."""
        ...


def _summary_order(record: ToolExecutionRecord) -> tuple[int, float]:
    priority = (
        0
        if not record.terminal
        else 1
        if record.result_available and not record.delivery_acknowledged
        else 2
        if record.status in {"failed", "cancelled", "lost"}
        else 3
    )
    return priority, -record.created_at.timestamp()


class MemoryToolExecutionStore:
    """Bound completed retention without evicting active operations."""

    def __init__(self, *, max_completed: int = 256) -> None:
        if max_completed < 1:
            raise ValueError("max_completed must be positive")
        self._max_completed = max_completed
        self._executions: dict[str, StoredToolExecution] = {}

    async def save(self, execution: StoredToolExecution) -> None:
        previous = self._executions.get(execution.record.execution_id)
        if previous is not None:
            # Foreground release and consumption are monotonic metadata, not
            # snapshots that an in-flight publication may reset.
            execution = replace(
                execution,
                record=execution.record.model_copy(
                    update={
                        "foreground_released": execution.record.foreground_released
                        or previous.record.foreground_released,
                        "cancel_requested": execution.record.cancel_requested or previous.record.cancel_requested,
                        "delivery_acknowledged": execution.record.delivery_acknowledged
                        or previous.record.delivery_acknowledged,
                    }
                ),
            )
        self._executions[execution.record.execution_id] = deepcopy(execution)
        completed = sorted(
            (value.record for value in self._executions.values() if value.record.terminal),
            key=lambda record: record.completed_at or record.created_at,
        )
        for record in completed[: -self._max_completed]:
            del self._executions[record.execution_id]

    async def get(self, thread_id: str, execution_id: str) -> StoredToolExecution | None:
        execution = self._executions.get(execution_id)
        if execution is None or execution.record.thread_id != thread_id:
            return None
        return deepcopy(execution)

    async def list(self, thread_id: str, *, offset: int = 0, limit: int = 20) -> tuple[ToolExecutionRecord, ...]:
        records = [value.record for value in self._executions.values() if value.record.thread_id == thread_id]
        return tuple(sorted(records, key=_summary_order)[offset : offset + limit])

    async def release_foreground(self, thread_id: str, execution_id: str) -> None:
        execution = self._executions.get(execution_id)
        if execution is not None and execution.record.thread_id == thread_id:
            self._executions[execution_id] = replace(
                execution, record=execution.record.model_copy(update={"foreground_released": True})
            )

    async def request_cancellation(self, thread_id: str, execution_id: str) -> None:
        execution = self._executions.get(execution_id)
        if execution is not None and execution.record.thread_id == thread_id and not execution.record.terminal:
            self._executions[execution_id] = replace(
                execution, record=execution.record.model_copy(update={"cancel_requested": True})
            )

    async def acknowledge_delivery(self, thread_id: str, execution_id: str) -> None:
        execution = self._executions.get(execution_id)
        if execution is not None and execution.record.thread_id == thread_id and execution.record.result_available:
            self._executions[execution_id] = replace(
                execution, record=execution.record.model_copy(update={"delivery_acknowledged": True})
            )


@dataclass
class _ExecutionSlot:
    done: asyncio.Future[None]
    predecessors: tuple[asyncio.Future[None], ...]
    sequential: bool

    async def wait(self) -> None:
        if self.predecessors:
            await asyncio.wait(self.predecessors)


_CURRENT_TOOL_EXECUTION: contextvars.ContextVar[ToolExecutionRecord | None] = contextvars.ContextVar(
    "a13n_current_tool_execution", default=None
)


def current_execution() -> ToolExecutionRecord | None:
    """The original invocation identity for Host authorization of retained dispatches."""
    return _CURRENT_TOOL_EXECUTION.get()


_EXECUTION_CLEANUP: contextvars.ContextVar[bool] = contextvars.ContextVar("a13n_execution_cleanup", default=False)


@contextmanager
def execution_cleanup() -> Iterator[None]:
    """Mark original-resource teardown for Host cleanup-only fencing, never new effects."""
    token = _EXECUTION_CLEANUP.set(True)
    try:
        yield
    finally:
        _EXECUTION_CLEANUP.reset(token)


def execution_cleanup_active() -> bool:
    return current_execution() is not None and _EXECUTION_CLEANUP.get()


@runtime_checkable
class ToolExecutionResource(Protocol):
    """A live resource's bounded control protocol; Hosts may route JSON calls to its owner."""

    async def call(self, request: dict[str, JsonValue]) -> dict[str, JsonValue]: ...


@dataclass
class _LiveExecution:
    record: ToolExecutionRecord
    task: asyncio.Task[None] | None = None
    started: bool = False
    cancel_reason: str | None = None
    recovering: bool = False
    releasing: bool = False
    resource: object | None = None
    resource_context: contextvars.Context | None = None
    shell: ShellExecutionState | None = None
    yield_requested: bool = False
    published: StoredToolExecution | None = None
    admitted: asyncio.Event = field(default_factory=asyncio.Event)
    store_context: contextvars.Context = field(default_factory=contextvars.copy_context)


class ToolExecutionRuntime:
    """Execute each admitted operation once, independently of foreground waits.

    Admission is synchronous so the caller receives the association before any
    effect or cancellation point. The owning task stores admission before calling
    the operation. ``release`` transfers an already prepared resource lease and
    runs even when cancellation wins before dispatch. Resource owners remain
    responsible for task-affine scope teardown.
    """

    def __init__(
        self,
        *,
        store: ToolExecutionStore | None = None,
        max_active: int = 64,
        on_completed: Callable[[ToolExecutionRecord], None] | None = None,
        reports_background_completions: bool = False,
    ) -> None:
        if max_active < 1:
            raise ValueError("max_active must be positive")
        self.store = store if store is not None else MemoryToolExecutionStore()
        self._max_active = max_active
        self._on_completed = on_completed
        self.reports_background_completions = reports_background_completions
        self._live: dict[str, _LiveExecution] = {}
        self._rejected = MemoryToolExecutionStore()
        self._closing = False
        self._order: dict[str, list[_ExecutionSlot]] = {}

    def _reserve(self, thread_id: str, *, sequential: bool) -> _ExecutionSlot:
        pending = self._order.setdefault(thread_id, [])
        slot = _ExecutionSlot(
            asyncio.get_running_loop().create_future(),
            tuple(
                dict.fromkeys(
                    dependency
                    for item in pending
                    if sequential or item.sequential
                    for dependency in (*item.predecessors, item.done)
                    if not dependency.done()
                )
            ),
            sequential,
        )
        pending.append(slot)
        return slot

    def _release_slot(self, thread_id: str, slot: _ExecutionSlot) -> None:
        slot.done.set_result(None)
        pending = self._order[thread_id]
        pending.remove(slot)
        if not pending:
            del self._order[thread_id]

    async def inline(self, operation: Callable[[], Awaitable[Any]], *, thread_id: str, sequential: bool) -> Any:
        """Preserve ordering for foreground-only calls alongside owned work."""
        slot = self._reserve(thread_id, sequential=sequential)
        try:
            await slot.wait()
            return await operation()
        finally:
            self._release_slot(thread_id, slot)

    def admit(
        self,
        operation: Callable[[], Awaitable[Any]],
        *,
        thread_id: str,
        run_id: str,
        tool_call_id: str,
        tool_name: str,
        release: Callable[[], Awaitable[None]] | None = None,
        sequential: bool = False,
    ) -> ToolExecutionRecord:
        if self._closing:
            raise system_failure("tool_runtime_closed", "Tool execution runtime is shutting down.")
        if len(self._live) >= self._max_active:
            raise system_failure(
                "tool_capacity_exceeded",
                "Tool execution capacity is full.",
                hint="Wait for an existing execution to finish.",
            )
        record = ToolExecutionRecord(
            execution_id=f"exec-{uuid4().hex[:16]}",
            thread_id=thread_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            sequential=sequential,
        )
        live = _LiveExecution(record)
        self._live[record.execution_id] = live
        slot = self._reserve(thread_id, sequential=sequential)
        live.task = asyncio.create_task(self._execute(live, operation, release, slot), name=record.execution_id)
        return record

    async def _execute(
        self,
        live: _LiveExecution,
        operation: Callable[[], Awaitable[Any]],
        release: Callable[[], Awaitable[None]] | None,
        slot: _ExecutionSlot,
        release_ownership: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        artifacts: list[ToolExecutionArtifact] = []
        owned_token = _OWNED_TOOL_EXECUTION.set(True)
        record_token = _CURRENT_TOOL_EXECUTION.set(live.record)
        artifacts_token = _TOOL_EXECUTION_ARTIFACTS.set(artifacts)
        try:
            try:
                await self._execute_owned(live, operation, release, slot, artifacts)
            finally:
                if release_ownership is not None:
                    await release_ownership()
        finally:
            live.admitted.set()
            _TOOL_EXECUTION_ARTIFACTS.reset(artifacts_token)
            _CURRENT_TOOL_EXECUTION.reset(record_token)
            _OWNED_TOOL_EXECUTION.reset(owned_token)
        # Host callbacks may start another Run. They must not inherit this
        # invocation's ownership, resource attachment or artifact collection.
        if self._on_completed is not None and live.record.terminal:
            try:
                self._on_completed(live.record)
            except Exception:
                get_logger(__name__).error("Tool execution completion observer failed: %s", live.record.execution_id)

    async def _execute_owned(
        self,
        live: _LiveExecution,
        operation: Callable[[], Awaitable[Any]],
        release: Callable[[], Awaitable[None]] | None,
        slot: _ExecutionSlot,
        artifacts: list[ToolExecutionArtifact],
    ) -> None:
        live.started = True
        result: Any = None
        status: Literal["succeeded", "failed", "cancelled", "lost"] = "failed"
        error: str | None = None
        retry_prompt: str | None = None
        rejected = False
        yielded = False
        try:
            try:
                await self.store.save(StoredToolExecution(live.record, shell=live.shell))
                live.admitted.set()
            except ToolExecutionAdmissionRejected:
                rejected = True
                raise
            if live.record.cancel_requested and live.shell is None:
                raise asyncio.CancelledError
            await slot.wait()
            await self.before_dispatch(live.record, sequential=slot.sequential)
            live.record = live.record.model_copy(update={"status": "running"})
            await self.store.save(StoredToolExecution(live.record, shell=live.shell))
            result = await operation()
            status = "succeeded"
        except asyncio.CancelledError:
            yielded = live.yield_requested and not live.record.cancel_requested
            status, error = "cancelled", live.cancel_reason
        except ToolExecutionSuspended:
            yielded = True
        except ToolExecutionLost as exc:
            status, error = "lost", str(exc)
        except ModelRetry as exc:
            retry_prompt = exc.message
            error = exc.message
        except Exception as exc:
            # Provider exceptions can contain credentials or raw response bodies.
            error = str(exc) if isinstance(exc, ToolFailed) else "Tool execution failed."
        finally:
            # Cancellation targets execution, not teardown. Keep the operation
            # live until its resource owner has actually finished draining.
            live.releasing = True
            live.resource = None
            if release is not None:
                try:
                    with execution_cleanup():
                        await release()
                except asyncio.CancelledError:
                    status, retry_prompt = "cancelled", None
                except Exception:
                    status, error = (
                        "failed",
                        tool_error_text(
                            system_error(
                                "tool_cleanup_failed",
                                "Tool execution resource cleanup failed.",
                                hint="Check the original operation before repeating a write.",
                            )
                        ),
                    )
                    retry_prompt = None
            self._release_slot(live.record.thread_id, slot)
        if yielded:
            if live.record.cancel_requested:
                try:
                    await self.store.request_cancellation(live.record.thread_id, live.record.execution_id)
                except Exception:
                    self._closing = True
                    get_logger(__name__).error(
                        "Tool cancellation intent could not be stored: %s", live.record.execution_id
                    )
                    return
            self._live.pop(live.record.execution_id, None)
            return
        if live.published is not None:
            live.record = live.published.record
            self._live.pop(live.record.execution_id, None)
            return
        await self._publish_terminal(live, status, error, result, artifacts, retry_prompt, rejected)

    async def _publish_terminal(
        self,
        live: _LiveExecution,
        status: Literal["succeeded", "failed", "cancelled", "lost"],
        error: str | None,
        result: Any,
        artifacts: list[ToolExecutionArtifact],
        retry_prompt: str | None,
        rejected: bool,
    ) -> None:
        terminal = live.record.model_copy(
            update={
                "status": status,
                "completed_at": datetime.now(UTC),
                "result_available": status == "succeeded",
                "error": error,
            }
        )
        saved = StoredToolExecution(
            terminal,
            result if status == "succeeded" else None,
            tuple(artifacts) if status == "succeeded" else (),
            retry_prompt=retry_prompt,
            shell=live.shell,
        )
        try:
            store = self._rejected if rejected else self.store
            await store.save(saved)
            # The foreground may yield while an asynchronous store publishes.
            if live.record.foreground_released and not terminal.foreground_released:
                terminal = terminal.model_copy(update={"foreground_released": True})
                await store.save(replace(saved, record=terminal))
        except Exception:
            # A broken store cannot safely admit more effects. Keep bounded
            # terminal evidence in the existing admission slot, not stale
            # running state or an unobserved task exception.
            self._closing = True
            live.record = terminal.model_copy(
                update={
                    "status": "lost",
                    "result_available": False,
                    "foreground_released": live.record.foreground_released,
                    "error": "Tool execution result could not be stored; runtime admission is closed.",
                }
            )
            get_logger(__name__).error("Tool execution result publication failed: %s", live.record.execution_id)
        else:
            live.record = terminal
            self._live.pop(live.record.execution_id, None)

    async def checkpoint_shell(self, state: ShellExecutionState) -> None:
        """Publish native recovery evidence before allowing the next effect."""
        record = current_execution()
        if record is None or (live := self._live.get(record.execution_id)) is None:
            raise RuntimeError("No active execution owns this checkpoint")
        try:
            await self.store.save(StoredToolExecution(live.record, shell=state))
        except Exception:
            if live.shell is not None:
                live.yield_requested = True
                raise ToolExecutionSuspended from None
            raise
        live.shell = state

    async def _spill_tool_result(self, data: bytes, *, suffix: str) -> str:
        """Retain recovered output without requiring a live Run's file area."""
        artifacts = _TOOL_EXECUTION_ARTIFACTS.get()
        if artifacts is None:
            raise RuntimeError("No active execution owns this artifact")
        path = f"tool-execution-artifact://{uuid4().hex}{suffix}"
        artifacts.append(ToolExecutionArtifact(path, data, suffix))
        return path

    async def publish_shell_result(self, result: Any) -> None:
        """Save captured output before its native process/output records are released."""
        record = current_execution()
        if record is None or (live := self._live.get(record.execution_id)) is None:
            raise RuntimeError("No active execution owns this result")
        if live.shell is None:
            return
        live.releasing = True
        terminal = live.record.model_copy(
            update={
                "status": "succeeded",
                "completed_at": datetime.now(UTC),
                "result_available": True,
            }
        )
        saved = StoredToolExecution(terminal, result, tuple(_TOOL_EXECUTION_ARTIFACTS.get() or ()), shell=live.shell)
        try:
            await self.store.save(saved)
            if live.record.foreground_released and not terminal.foreground_released:
                saved = replace(saved, record=terminal.model_copy(update={"foreground_released": True}))
                await self.store.save(saved)
        except Exception:
            live.yield_requested = True
            raise ToolExecutionSuspended from None
        live.published = saved

    def suspend_shell(self, execution_id: str) -> None:
        """Yield an uncertain native outcome only when pre-effect evidence exists."""
        live = self._live.get(execution_id)
        if live is not None and live.shell is not None:
            live.yield_requested = True
            raise ToolExecutionSuspended

    def preserving_shell(self, execution_id: str) -> bool:
        """Keep native evidence whenever supervision yields, including pending cancellation."""
        live = self._live.get(execution_id)
        return live is not None and live.yield_requested

    def cancellation_requested(self, execution_id: str) -> bool:
        live = self._live.get(execution_id)
        return live is not None and live.record.cancel_requested

    def yielding(self, execution_id: str) -> bool:
        live = self._live.get(execution_id)
        return live is not None and live.yield_requested and not live.record.cancel_requested

    def yield_shell(self, execution_id: str) -> bool:
        """Stop one native supervisor without converting owner loss into cancellation."""
        live = self._live.get(execution_id)
        if live is None or live.shell is None or live.releasing or live.record.cancel_requested:
            return False
        live.yield_requested = True
        if live.task is not None:
            live.task.cancel()
        return True

    def yield_recoverable(self) -> None:
        """Relinquish native supervision; explicit cancellation always takes precedence."""
        for execution_id in tuple(self._live):
            self.yield_shell(execution_id)

    async def relinquish(self) -> None:
        """Yield native supervisors and cancel their readers before joining cleanup."""
        self.begin_close(preserve_recoverable=True)
        await self.join()

    def resume_shell(
        self,
        execution: StoredToolExecution,
        operation: Callable[[], Awaitable[Any]],
        *,
        release_ownership: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Resume without a foreground waiter; release ownership after publication."""
        record = execution.record.model_copy(update={"foreground_released": True})
        if record.terminal or execution.shell is None:
            raise ValueError("Only unfinished native Shell executions can resume")
        if self._closing or record.execution_id in self._live or len(self._live) >= self._max_active:
            raise ToolFailed("Tool execution runtime cannot accept recovered supervision.")
        live = _LiveExecution(record=record, shell=execution.shell, recovering=True)
        self._live[record.execution_id] = live
        # This operation already started before any newly queued invocation.
        # Track its completion for future calls without waiting on later barriers.
        slot = _ExecutionSlot(asyncio.get_running_loop().create_future(), (), record.sequential)
        self._order.setdefault(record.thread_id, []).append(slot)
        live.task = asyncio.create_task(
            self._execute(live, operation, None, slot, release_ownership), name=record.execution_id
        )

    async def before_dispatch(self, record: ToolExecutionRecord, *, sequential: bool) -> None:
        """Allow the Host to wait for shared predecessors while the call remains queued."""

    def attach_resource(self, resource: object) -> None:
        """Attach a live control surface to the currently owned operation.

        The operation owns synchronization and closes the surface before returning.
        Resources never enter the store or outlive operation teardown.
        """
        record = _CURRENT_TOOL_EXECUTION.get()
        if record is None or (live := self._live.get(record.execution_id)) is None or live.releasing:
            raise RuntimeError("No active execution owns this resource")
        live.recovering = False
        live.resource = resource
        live.resource_context = contextvars.copy_context()

    def observe_resource(self, observation: object) -> None:
        """Offer a live observation to the Host without I/O or waiting.

        Hosts may coalesce these ephemeral snapshots independently of execution
        and lease supervision. The default runtime does not retain observations.
        """

    def resource(self, thread_id: str, execution_id: str) -> object | None:
        """Resolve a live resource only inside its exact owning Thread."""
        live = self._live.get(execution_id)
        if live is None or live.record.thread_id != thread_id or live.releasing:
            return None
        return live.resource

    async def call_resource(
        self, thread_id: str, execution_id: str, request: dict[str, JsonValue]
    ) -> dict[str, JsonValue] | None:
        """Call the original live resource, never reconstructing it from stored results.

        The caller owns authorization. The resource owns dispatch validation and
        synchronization with teardown. A missing surface returns None.
        """
        resource = self.resource(thread_id, execution_id)
        if not isinstance(resource, ToolExecutionResource):
            return None
        live = self._live[execution_id]
        assert live.resource_context is not None
        context = live.resource_context.copy()
        context.run(_TOOL_EXECUTION_ARTIFACTS.set, None)
        return await asyncio.create_task(resource.call(request), context=context)

    async def release_foreground(self, thread_id: str, execution_id: str) -> None:
        """Persist receipt delivery before returning it to the foreground."""
        live = self._live.get(execution_id)
        if live is not None:
            if live.record.thread_id != thread_id:
                return
            live.record = live.record.model_copy(update={"foreground_released": True})
            if live.published is not None:
                live.published = replace(
                    live.published, record=live.published.record.model_copy(update={"foreground_released": True})
                )
            await live.admitted.wait()
        store = self._rejected if await self._rejected.get(thread_id, execution_id) is not None else self.store
        await asyncio.create_task(
            store.release_foreground(thread_id, execution_id),
            context=live.store_context.copy() if live is not None else None,
        )
        if live is None and self._on_completed is not None:
            saved = await store.get(thread_id, execution_id)
            if saved is not None and saved.record.terminal:
                self._on_completed(saved.record)

    async def info(self, thread_id: str, execution_id: str) -> ToolExecutionRecord | None:
        if (live := self._live.get(execution_id)) is not None:
            return live.record if live.record.thread_id == thread_id else None
        execution = await self.result(thread_id, execution_id)
        return execution.record if execution is not None else None

    async def result(self, thread_id: str, execution_id: str) -> StoredToolExecution | None:
        if (live := self._live.get(execution_id)) is not None:
            return StoredToolExecution(live.record, shell=live.shell) if live.record.thread_id == thread_id else None
        return await self._rejected.get(thread_id, execution_id) or await self.store.get(thread_id, execution_id)

    async def wait(
        self, thread_id: str, execution_id: str, *, timeout_seconds: float | None = None
    ) -> StoredToolExecution | None:
        live = self._live.get(execution_id)
        if live is not None and live.record.thread_id == thread_id and live.task is not None:
            # asyncio.wait does not cancel the owned task on expiry or caller cancellation.
            await asyncio.wait({live.task}, timeout=timeout_seconds)
            if not live.task.done():
                return StoredToolExecution(live.record, shell=live.shell)
            live.task.result()
        return await self.result(thread_id, execution_id)

    def request_cancellation(
        self, thread_id: str, execution_id: str, *, reason: str | None = None
    ) -> ToolExecutionRecord | None:
        """Cancel a locally owned invocation without storage I/O or interrupting its cleanup."""
        live = self._live.get(execution_id)
        if live is not None and live.record.thread_id == thread_id:
            if not live.record.cancel_requested:
                live.record = live.record.model_copy(update={"cancel_requested": True})
                live.cancel_reason = reason
                # A recovered native job must first regain its control surface.
                # Cancelling activation would publish cancellation without killing it.
                if live.started and not live.releasing and not live.recovering and live.task is not None:
                    live.task.cancel()
            return live.record
        return None

    async def cancel(self, thread_id: str, execution_id: str) -> ToolExecutionRecord | None:
        live = self._live.get(execution_id)
        if live is not None and live.record.thread_id != thread_id:
            return None
        self.request_cancellation(thread_id, execution_id)
        if live is not None:
            await live.admitted.wait()
        await self.store.request_cancellation(thread_id, execution_id)
        return await self.info(thread_id, execution_id)

    @property
    def accepting(self) -> bool:
        """Whether this runtime can admit new effects, including after publication failure."""
        return not self._closing

    @property
    def active(self) -> tuple[ToolExecutionRecord, ...]:
        """Process-local invocations, including resource cleanup and publication in flight."""
        return tuple(live.record for live in self._live.values() if live.task is not None and not live.task.done())

    async def acknowledge_delivery(self, thread_id: str, execution_id: str) -> None:
        if await self._rejected.get(thread_id, execution_id) is not None:
            await self._rejected.acknowledge_delivery(thread_id, execution_id)
        else:
            await self.store.acknowledge_delivery(thread_id, execution_id)

    async def summarize(self, thread_id: str, *, offset: int = 0, limit: int = 20) -> tuple[ToolExecutionRecord, ...]:
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("offset must be nonnegative and limit must be between 1 and 100")
        records = {
            record.execution_id: record for record in await self.store.list(thread_id, offset=0, limit=offset + limit)
        }
        records.update(
            {
                record.execution_id: record
                for record in await self._rejected.list(thread_id, offset=0, limit=offset + limit)
            }
        )
        records.update({key: live.record for key, live in self._live.items() if live.record.thread_id == thread_id})
        return tuple(sorted(records.values(), key=_summary_order)[offset : offset + limit])

    async def join(self) -> None:
        """Join retained scopes before their Host closes shared resources."""
        tasks = {live.task for live in self._live.values() if live.task is not None}
        if tasks:
            await asyncio.wait(tasks)
            for task in tasks:
                task.result()

    def stop_admission(self) -> None:
        """Begin Host drain without cancelling already admitted work."""
        self._closing = True

    def begin_close(self, *, preserve_recoverable: bool = False, reason: str | None = None) -> None:
        """Signal every owner before joining any cleanup that can depend on another."""
        self.stop_admission()
        if preserve_recoverable:
            self.yield_recoverable()
        for live in tuple(self._live.values()):
            if not (preserve_recoverable and live.yield_requested):
                self.request_cancellation(live.record.thread_id, live.record.execution_id, reason=reason)

    async def close(
        self, *, timeout_seconds: float = 5.0, reason: str | None = None, preserve_recoverable: bool = False
    ) -> tuple[str, ...]:
        """Signal shutdown and wait boundedly; report operations still draining."""
        self.begin_close(preserve_recoverable=preserve_recoverable, reason=reason)
        tasks = {live.task for live in self._live.values() if live.task is not None}
        if tasks:
            done, _ = await asyncio.wait(tasks, timeout=timeout_seconds)
            for task in done:
                task.result()
        return tuple(key for key, live in self._live.items() if live.task is not None and not live.task.done())
