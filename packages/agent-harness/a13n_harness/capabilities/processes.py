"""Shell execution operators and detached in-memory process management."""

from __future__ import annotations

import asyncio
import inspect
import secrets
import weakref
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, ClassVar, Literal, Protocol, cast, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from a13n_harness.context import AgentContext
from a13n_harness.environment.commands import CommandRequest, ProcessStatus, ShellExecResult
from a13n_harness.environment.models import EnvironmentError

_TERMINAL_PHASES = frozenset({"exited", "signaled", "timed_out", "cancelled", "failed"})

type ProcessEventKind = Literal["completion", "gap"]
type ProcessBackendEventHook = Callable[[ProcessExecutionSnapshot], Awaitable[None]]
type ProcessEventHook = Callable[[ProcessEvent], Awaitable[None]]
type ProcessLauncher = Callable[[AgentContext, CommandRequest, str | None], Awaitable[ManagedProcess]]


class ProcessExecutionSnapshot(BaseModel):
    """Detached current projection of one operator-owned process."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    backend_id: str = Field(min_length=1, max_length=512)
    status: ProcessStatus
    stdin_open: bool
    stdout_produced_bytes: int = Field(default=0, ge=0)
    stderr_produced_bytes: int = Field(default=0, ge=0)


class ProcessOutputChunk(BaseModel):
    """One portable retained-output page without provider handles or cursors."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    data: bytes = b""
    start_offset: int = Field(default=0, ge=0)
    available_start: int = Field(default=0, ge=0)
    available_end: int = Field(default=0, ge=0)
    producer_complete: bool = False
    content_complete: bool = True

    @model_validator(mode="after")
    def _validate_offsets(self) -> ProcessOutputChunk:
        if not self.available_start <= self.start_offset <= self.available_end:
            raise ValueError("process output page start is outside the available range")
        if self.start_offset + len(self.data) > self.available_end:
            raise ValueError("process output page exceeds the available range")
        return self


class ProcessOutputPage(BaseModel):
    """Current process snapshot and one page from each output stream."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    snapshot: ProcessExecutionSnapshot
    stdout: ProcessOutputChunk = ProcessOutputChunk()
    stderr: ProcessOutputChunk = ProcessOutputChunk()


class ProcessInputSnapshot(BaseModel):
    """Accepted stdin bytes and the resulting process snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    accepted_bytes: int = Field(ge=0)
    snapshot: ProcessExecutionSnapshot


class ProcessSignalSnapshot(BaseModel):
    """Signal acceptance and the resulting process snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    accepted: bool
    snapshot: ProcessExecutionSnapshot


@dataclass(frozen=True, slots=True)
class ProcessEvent:
    """Correlated non-authoritative observation delivered to stable Host hooks."""

    kind: ProcessEventKind
    thread_id: str
    run_id: str
    agent_instance_id: str
    host_refs: Mapping[str, str]
    process_id: str
    backend_id: str
    status: ProcessStatus


@runtime_checkable
class ManagedProcess(Protocol):
    """Detached process object whose methods remain valid after the parent Run closes."""

    @property
    def backend_id(self) -> str: ...

    async def inspect(self) -> ProcessExecutionSnapshot: ...

    async def wait(self) -> ProcessExecutionSnapshot: ...

    async def read_output(
        self,
        stdout_offset: int,
        stderr_offset: int,
        wait_seconds: float,
        max_bytes: int,
    ) -> ProcessOutputPage: ...

    async def write_stdin(self, data: bytes, close_after_write: bool) -> ProcessInputSnapshot: ...

    async def signal(self, signal: Literal["interrupt", "terminate"]) -> ProcessSignalSnapshot: ...

    async def kill(self) -> ProcessExecutionSnapshot: ...

    async def force_close(self) -> None:
        """Force termination when live and release detached process resources."""
        ...


class ShellOperator:
    """Public shell execution boundary used by the standard shell Toolset."""

    supports_background: ClassVar[bool] = False

    async def execute(
        self,
        context: AgentContext,
        request: CommandRequest,
        alias: str | None,
    ) -> ShellExecResult:
        return await context.environment.shell.exec_captured(request, alias=alias)

    async def start(
        self,
        context: AgentContext,
        request: CommandRequest,
        alias: str | None,
        process_id: str,
        observer: ProcessBackendEventHook,
    ) -> ProcessExecutionSnapshot:
        del context, request, alias, process_id, observer
        raise EnvironmentError("Background process operations are unavailable.", code="environment_unsupported")

    async def rebind(
        self,
        context: AgentContext,
        backend_id: str,
        observer: ProcessBackendEventHook,
    ) -> ProcessExecutionSnapshot | None:
        del context, backend_id, observer
        return None

    async def inspect(self, context: AgentContext, backend_id: str) -> ProcessExecutionSnapshot | None:
        del context, backend_id
        return None

    async def read_output(
        self,
        context: AgentContext,
        backend_id: str,
        stdout_offset: int,
        stderr_offset: int,
        wait_seconds: float,
        max_bytes: int,
    ) -> ProcessOutputPage | None:
        del context, backend_id, stdout_offset, stderr_offset, wait_seconds, max_bytes
        return None

    async def write_stdin(
        self,
        context: AgentContext,
        backend_id: str,
        data: bytes,
        close_after_write: bool,
    ) -> ProcessInputSnapshot | None:
        del context, backend_id, data, close_after_write
        return None

    async def signal(
        self,
        context: AgentContext,
        backend_id: str,
        signal: Literal["interrupt", "terminate"],
    ) -> ProcessSignalSnapshot | None:
        del context, backend_id, signal
        return None

    async def kill(self, context: AgentContext, backend_id: str) -> ProcessExecutionSnapshot | None:
        del context, backend_id
        return None

    async def force_close(self) -> None:
        """Reject admission, force-stop owned work, and finish only its cleanup."""


class _WeakObserver:
    def __init__(self, callback: ProcessBackendEventHook) -> None:
        if inspect.ismethod(callback):
            self._reference: weakref.ReferenceType[Any] = weakref.WeakMethod(callback)
        else:
            try:
                self._reference = weakref.ref(callback)
            except TypeError as exc:
                raise TypeError("process observer must support weak references") from exc

    def get(self) -> ProcessBackendEventHook | None:
        return cast(ProcessBackendEventHook | None, self._reference())


@dataclass(slots=True)
class _CanonicalProcess:
    process: ManagedProcess
    process_id: str
    parent_thread_id: str
    parent_run_id: str
    parent_agent_instance_id: str
    parent_host_refs: Mapping[str, str]
    observer: _WeakObserver
    watcher: asyncio.Task[None] | None = None
    notified: ProcessEventKind | None = None


class ProcessManager(ShellOperator):
    """Default background-capable operator owning detached process objects in memory."""

    supports_background: ClassVar[bool] = True

    def __init__(
        self,
        launcher: ProcessLauncher,
        *,
        event_hooks: Sequence[ProcessEventHook] = (),
    ) -> None:
        if not callable(launcher):
            raise TypeError("launcher must be callable")
        hooks = tuple(event_hooks)
        if not all(callable(hook) for hook in hooks):
            raise TypeError("event_hooks must contain callables")
        self._launcher = launcher
        self._event_hooks = hooks
        self._records: dict[str, _CanonicalProcess] = {}
        self._delivery_tasks: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()
        self._force_close_task: asyncio.Task[None] | None = None
        self._closed = False

    async def start(
        self,
        context: AgentContext,
        request: CommandRequest,
        alias: str | None,
        process_id: str,
        observer: ProcessBackendEventHook,
    ) -> ProcessExecutionSnapshot:
        async with self._lock:
            if self._closed:
                raise EnvironmentError("Process manager is closing.", code="environment_closed")
        process: ManagedProcess | None = None
        record: _CanonicalProcess | None = None
        registered = False
        try:
            candidate = await self._launcher(context, request, alias)
            if not isinstance(candidate, ManagedProcess):
                raise TypeError("process launcher must return a detached ManagedProcess")
            process = candidate
            backend_id = process.backend_id
            if not isinstance(backend_id, str) or not backend_id or len(backend_id) > 512:
                raise TypeError("managed process backend_id is invalid")
            snapshot = await process.inspect()
            _validate_snapshot(snapshot, backend_id)
            record = _CanonicalProcess(
                process=process,
                process_id=process_id,
                parent_thread_id=context.thread_id,
                parent_run_id=context.run_id,
                parent_agent_instance_id=context.instance.agent_instance_id,
                parent_host_refs=MappingProxyType(dict(context.instance.host_refs)),
                observer=_WeakObserver(observer),
            )
            async with self._lock:
                if self._closed:
                    raise EnvironmentError("Process manager is closing.", code="environment_closed")
                if backend_id in self._records:
                    raise EnvironmentError("Process backend identity is already active.", code="environment_conflict")
                record.watcher = asyncio.create_task(self._watch(record), name=f"process-{secrets.token_hex(6)}")
                self._records[backend_id] = record
                registered = True
            return snapshot
        except BaseException:
            if registered and record is not None and process is not None:
                async with self._lock:
                    if self._records.get(process.backend_id) is record:
                        self._records.pop(process.backend_id, None)
                if record.watcher is not None:
                    record.watcher.cancel()
            if process is not None:
                await _close_failed_process(process)
            raise

    async def rebind(
        self,
        context: AgentContext,
        backend_id: str,
        observer: ProcessBackendEventHook,
    ) -> ProcessExecutionSnapshot | None:
        del context
        async with self._lock:
            record = self._records.get(backend_id)
            if record is None:
                return None
            record.observer = _WeakObserver(observer)
        return await record.process.inspect()

    async def inspect(self, context: AgentContext, backend_id: str) -> ProcessExecutionSnapshot | None:
        del context
        async with self._lock:
            record = self._records.get(backend_id)
        return None if record is None else await record.process.inspect()

    async def read_output(
        self,
        context: AgentContext,
        backend_id: str,
        stdout_offset: int,
        stderr_offset: int,
        wait_seconds: float,
        max_bytes: int,
    ) -> ProcessOutputPage | None:
        del context
        record = await self._record(backend_id)
        if record is None:
            return None
        page = await record.process.read_output(
            stdout_offset,
            stderr_offset,
            wait_seconds,
            max_bytes,
        )
        _validate_output_page(
            page,
            backend_id,
            stdout_offset=stdout_offset,
            stderr_offset=stderr_offset,
            max_bytes=max_bytes,
        )
        return page

    async def write_stdin(
        self,
        context: AgentContext,
        backend_id: str,
        data: bytes,
        close_after_write: bool,
    ) -> ProcessInputSnapshot | None:
        del context
        record = await self._record(backend_id)
        if record is None:
            return None
        result = await record.process.write_stdin(data, close_after_write)
        _validate_snapshot(result.snapshot, backend_id)
        return result

    async def signal(
        self,
        context: AgentContext,
        backend_id: str,
        signal: Literal["interrupt", "terminate"],
    ) -> ProcessSignalSnapshot | None:
        del context
        record = await self._record(backend_id)
        if record is None:
            return None
        result = await record.process.signal(signal)
        _validate_snapshot(result.snapshot, backend_id)
        return result

    async def kill(self, context: AgentContext, backend_id: str) -> ProcessExecutionSnapshot | None:
        del context
        record = await self._record(backend_id)
        if record is None:
            return None
        snapshot = await record.process.kill()
        _validate_snapshot(snapshot, backend_id)
        return snapshot

    async def wait_idle(self) -> None:
        """Wait until all currently admitted processes reach a terminal state."""

        while True:
            async with self._lock:
                watchers = tuple(
                    record.watcher
                    for record in self._records.values()
                    if record.watcher is not None and not record.watcher.done()
                )
            if not watchers:
                return
            await asyncio.gather(*watchers, return_exceptions=True)

    async def force_close(self) -> None:
        async with self._lock:
            close_task = self._force_close_task
            if close_task is None:
                self._closed = True
                close_task = asyncio.create_task(self._force_close_owned(), name="process-manager-force-close")
                self._force_close_task = close_task
        cancelled, error = await _await_owned_task(close_task)
        if cancelled:
            raise asyncio.CancelledError
        if error is not None:
            raise error

    async def _force_close_owned(self) -> None:
        async with self._lock:
            records = tuple(self._records.values())
        results = await asyncio.gather(
            *(record.process.force_close() for record in records),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, BaseException)]
        watchers = tuple(record.watcher for record in records if record.watcher is not None)
        if watchers:
            for watcher in watchers:
                if not watcher.done():
                    watcher.cancel()
            await asyncio.gather(*watchers, return_exceptions=True)
        while self._delivery_tasks:
            deliveries = tuple(self._delivery_tasks)
            await asyncio.gather(*deliveries, return_exceptions=True)
            self._delivery_tasks.difference_update(deliveries)
        async with self._lock:
            self._records.clear()
        if errors:
            raise BaseExceptionGroup("Managed process cleanup failed.", errors)

    async def _record(self, backend_id: str) -> _CanonicalProcess | None:
        async with self._lock:
            return self._records.get(backend_id)

    async def _watch(self, record: _CanonicalProcess) -> None:
        kind: ProcessEventKind = "completion"
        try:
            snapshot = await record.process.wait()
            _validate_snapshot(snapshot, record.process.backend_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            kind = "gap"
            snapshot = ProcessExecutionSnapshot(
                backend_id=record.process.backend_id,
                status=ProcessStatus(
                    phase="failed",
                    termination_reason="backend_lost",
                    ended_at=datetime.now(UTC),
                    cleanup="failed",
                ),
                stdin_open=False,
            )
        if kind == "completion" and snapshot.status.phase not in _TERMINAL_PHASES:
            return
        self._notify(record, snapshot, kind)

    def _notify(
        self,
        record: _CanonicalProcess,
        snapshot: ProcessExecutionSnapshot,
        kind: ProcessEventKind,
    ) -> None:
        observer = record.observer.get()
        if observer is not None:
            detached = snapshot.model_copy(deep=True)
            self._spawn_delivery(lambda: observer(detached))
        if record.notified == "completion" or record.notified == kind:
            return
        record.notified = kind
        event = ProcessEvent(
            kind=kind,
            thread_id=record.parent_thread_id,
            run_id=record.parent_run_id,
            agent_instance_id=record.parent_agent_instance_id,
            host_refs=record.parent_host_refs,
            process_id=record.process_id,
            backend_id=snapshot.backend_id,
            status=snapshot.status.model_copy(deep=True),
        )
        for hook in self._event_hooks:
            self._spawn_delivery(lambda hook=hook: hook(event))

    def _spawn_delivery(self, delivery: Callable[[], Awaitable[None]]) -> None:
        async def deliver() -> None:
            try:
                await delivery()
            except Exception:
                pass

        task = asyncio.create_task(deliver(), name=f"process-delivery-{secrets.token_hex(6)}")
        self._delivery_tasks.add(task)
        task.add_done_callback(self._delivery_tasks.discard)


def _validate_snapshot(snapshot: ProcessExecutionSnapshot, backend_id: str) -> None:
    if not isinstance(snapshot, ProcessExecutionSnapshot) or snapshot.backend_id != backend_id:
        raise EnvironmentError("Process operator retargeted a managed process.", code="environment_provider_failure")


def _validate_output_page(
    page: ProcessOutputPage,
    backend_id: str,
    *,
    stdout_offset: int,
    stderr_offset: int,
    max_bytes: int,
) -> None:
    if not isinstance(page, ProcessOutputPage):
        raise TypeError("managed process returned an invalid output page")
    _validate_snapshot(page.snapshot, backend_id)
    if len(page.stdout.data) + len(page.stderr.data) > max_bytes:
        raise EnvironmentError("Managed process exceeded the output page budget.", code="environment_provider_failure")
    _validate_output_chunk(
        page.stdout,
        requested_offset=stdout_offset,
        produced_bytes=page.snapshot.stdout_produced_bytes,
    )
    _validate_output_chunk(
        page.stderr,
        requested_offset=stderr_offset,
        produced_bytes=page.snapshot.stderr_produced_bytes,
    )


def _validate_output_chunk(
    chunk: ProcessOutputChunk,
    *,
    requested_offset: int,
    produced_bytes: int,
) -> None:
    if requested_offset > produced_bytes or chunk.available_end != produced_bytes:
        raise EnvironmentError(
            "Managed process returned inconsistent output offsets.", code="environment_provider_failure"
        )
    expected_start = max(requested_offset, chunk.available_start)
    if chunk.start_offset != expected_start:
        raise EnvironmentError(
            "Managed process returned a noncontiguous output page.", code="environment_provider_failure"
        )


async def _await_owned_task(task: asyncio.Task[Any]) -> tuple[bool, BaseException | None]:
    """Wait for owned work without letting repeated caller cancellation cancel it."""
    cancelled = False
    while True:
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if task.cancelled():
                return cancelled, exc
            cancelled = True
            continue
        except BaseException as exc:
            return cancelled, exc
        return cancelled, None


async def _close_failed_process(process: ManagedProcess) -> None:
    task = asyncio.create_task(process.force_close())
    cancelled, _ = await _await_owned_task(task)
    if cancelled:
        raise asyncio.CancelledError


__all__ = [
    "ManagedProcess",
    "ProcessBackendEventHook",
    "ProcessEvent",
    "ProcessEventHook",
    "ProcessEventKind",
    "ProcessExecutionSnapshot",
    "ProcessInputSnapshot",
    "ProcessLauncher",
    "ProcessManager",
    "ProcessOutputChunk",
    "ProcessOutputPage",
    "ProcessSignalSnapshot",
    "ShellOperator",
]
