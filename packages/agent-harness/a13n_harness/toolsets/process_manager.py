"""Portable Agent process state and runtime operations over Environment processes."""

from __future__ import annotations

import asyncio
import re
from collections import OrderedDict
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_serializer, field_validator
from pydantic_ai import RunContext

from a13n_harness._json import redact_json
from a13n_harness.context import AgentContext
from a13n_harness.environment.commands import (
    BoundProcessHandle,
    CommandRequest,
    ProcessIdentity,
    ProcessInfo,
    ProcessStatus,
)
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.providers import BoundOutputOperations, BoundProcessOperations
from a13n_harness.environment.retention import EnvironmentOutputCapture, EnvironmentOutputPolicy
from a13n_harness.state import AgentContextState

from .output import (
    DEFAULT_TOOL_OUTPUT_CHARS,
    acknowledge_tool_output,
    continuation_disclosure,
    tool_output_size,
)
from .shell_results import (
    OutputCaptureProjection,
    ProcessProjection,
    ProcessReadOutputResult,
    ProcessStatusItem,
    ProcessStatusListSuccess,
    ProcessStatusProjection,
)

_MAX_MODEL_TEXT_BYTES = 256 * 1024
_MAX_MODEL_OUTPUT_BYTES = 1024 * 1024
_MAX_REFERENCE_ENTRIES = 100_000
_PROCESS_RESULT_ENVELOPE_CHARS = 128
_REFERENCE_PATTERN = re.compile(r"^process-([1-9][0-9]*)$")
_TERMINAL_PHASES = frozenset({"exited", "signaled", "timed_out", "cancelled", "failed"})
_MAX_NOTIFICATION_ITEMS = 16
_PROCESS_STATE_VERSION = "1"
PROCESS_STATE_ID = "a13n.dynamic-environment.processes"
_REBIND_OUTPUT_POLICY = EnvironmentOutputPolicy(
    max_inline_bytes=64 * 1024,
    max_output_bytes=_MAX_MODEL_OUTPUT_BYTES,
    overflow="retain",
)


type ProcessEventKind = Literal["completion", "gap"]


class ManagedProcessState(BaseModel):
    """Portable Agent projection of one provider-owned process."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: ProcessIdentity
    mount_id: str | None = Field(default=None, max_length=256)
    stdout_offset: int = Field(default=0, ge=0)
    stderr_offset: int = Field(default=0, ge=0)
    status: ProcessStatus
    stdin_open: bool
    stdout_produced_bytes: int = Field(default=0, ge=0)
    stderr_produced_bytes: int = Field(default=0, ge=0)
    backend_lost: bool = False


class ProcessManagerState(BaseModel):
    """Versioned process-N mapping stored in AgentContext State."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    next_sequence: int = Field(default=1, ge=1, le=_MAX_REFERENCE_ENTRIES + 1)
    processes: Mapping[str, ManagedProcessState] = Field(default_factory=dict)

    @field_validator("processes", mode="after")
    @classmethod
    def _validate_processes(
        cls,
        value: Mapping[str, ManagedProcessState],
    ) -> Mapping[str, ManagedProcessState]:
        copied = dict(value)
        if len(copied) > _MAX_REFERENCE_ENTRIES:
            raise ValueError("process state exceeds the finite reference limit")
        if any(_REFERENCE_PATTERN.fullmatch(process_id) is None for process_id in copied):
            raise ValueError("process state contains an invalid compact reference")
        return MappingProxyType(copied)

    @field_serializer("processes")
    def _serialize_processes(
        self,
        value: Mapping[str, ManagedProcessState],
    ) -> dict[str, ManagedProcessState]:
        return dict(value)


@dataclass(frozen=True, slots=True)
class ProcessEvent:
    """Correlated non-authoritative process observation delivered to Host hooks."""

    kind: ProcessEventKind
    thread_id: str
    run_id: str
    agent_instance_id: str
    process_id: str
    identity: ProcessIdentity
    status: ProcessStatus


type ProcessEventHook = Callable[[ProcessEvent], Awaitable[None]]


@dataclass(slots=True)
class _ProcessEntry:
    process_id: str
    state: ManagedProcessState
    handle: BoundProcessHandle | None = None
    cleanup_info: ProcessInfo | None = field(default=None, repr=False)
    drain_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    observer: asyncio.Task[None] | None = field(default=None, repr=False)


class ProcessManager:
    """Operate portable Agent process state against the current Environment."""

    def __init__(
        self,
        *,
        processes: BoundProcessOperations,
        outputs: BoundOutputOperations,
        max_reference_entries: int,
        process_event_hooks: Sequence[ProcessEventHook] = (),
        execution_guard: Callable[[], None] | None = None,
    ) -> None:
        if not 0 < max_reference_entries <= _MAX_REFERENCE_ENTRIES:
            raise ValueError(f"max_reference_entries must be between 1 and {_MAX_REFERENCE_ENTRIES}")
        hooks = tuple(process_event_hooks)
        if not all(callable(hook) for hook in hooks):
            raise TypeError("process_event_hooks must contain callables")
        self._processes = processes
        self._outputs = outputs
        self._max_reference_entries = max_reference_entries
        self._event_hooks = hooks
        self._execution_guard = execution_guard
        self._entries: OrderedDict[str, _ProcessEntry] = OrderedDict()
        self._next_sequence = 1
        self._state_store: AgentContextState | None = None
        self._event_context: tuple[str, str, str] | None = None
        self._state_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._active_context: RunContext[AgentContext] | None = None
        self._pending: OrderedDict[str, ProcessEventKind] = OrderedDict()
        self._notified: dict[str, ProcessEventKind] = {}
        self._loaded = False
        self._closed = False

    @asynccontextmanager
    async def active_run(self, ctx: RunContext[AgentContext]) -> AsyncGenerator[None]:
        """Bind AgentContext State and enable Turn-scoped completion notices."""
        if self._closed:
            raise RuntimeError("ProcessManager is closed")
        await self._load(ctx.deps.state)
        event_context = (
            ctx.deps.thread_id,
            ctx.deps.run_id,
            ctx.deps.instance.agent_instance_id,
        )
        if self._event_context is None:
            self._event_context = event_context
        elif self._event_context != event_context:
            raise RuntimeError("ProcessManager cannot cross AgentContext run identities")
        owned = self._active_context is None
        if owned:
            self._active_context = ctx
            for entry in tuple(self._entries.values()):
                self._ensure_observer(entry)
            self._flush_notifications()
        try:
            yield
        finally:
            if owned and self._active_context is ctx:
                self._active_context = None
                await self._cancel_observers()

    async def close(self) -> None:
        """Stop observation and finish already-drained terminal cleanup."""
        if self._closed:
            return
        self._closed = True
        self._active_context = None
        await self._cancel_observers()
        cleanup_failed = False
        for entry in tuple(self._entries.values()):
            if entry.cleanup_info is None:
                continue
            async with entry.drain_lock:
                if entry.cleanup_info is not None and not await self._finish_release(entry, entry.cleanup_info):
                    cleanup_failed = True
        self._pending.clear()
        if cleanup_failed:
            raise EnvironmentError(
                "One or more drained terminal processes could not be released.",
                code="environment_cleanup_failed",
                retry_hint="dependency_change",
            )

    async def start(self, request: CommandRequest, *, alias: str | None) -> ProcessProjection:
        self._guard_execution()
        self._require_loaded()
        async with self._start_lock:
            if len(self._entries) >= self._max_reference_entries or self._next_sequence > self._max_reference_entries:
                raise EnvironmentError(
                    "Environment compact reference capacity is exhausted.",
                    code="environment_reference_exhausted",
                )
            result = await self._processes.start(request, alias=alias)
            process_id = f"process-{self._next_sequence}"
            entry = _ProcessEntry(
                process_id=process_id,
                handle=result.process.handle,
                state=self._state_from_info(result.process),
            )
            try:
                projected = self._project_process(
                    entry,
                    result.process,
                    consume_output=True,
                    output_budget=request.output_policy.max_inline_bytes,
                )
                self._entries[process_id] = entry
                self._next_sequence += 1
                await self._persist()
            except BaseException:
                await asyncio.shield(self._cleanup_unexposed(result.process))
                self._entries.pop(process_id, None)
                raise
        self._ensure_observer(entry)
        self._mark_release_if_complete(entry, result.process)
        return projected

    def identity(self, process_id: str) -> ProcessIdentity:
        return self._entry(process_id).state.identity

    async def resolve(self, process_id: str) -> BoundProcessHandle:
        return await self._handle(await self._prepare_entry(process_id))

    async def wait(
        self,
        process_id: str,
        *,
        timeout_seconds: float,
        max_inline_bytes: int = 64 * 1024,
        max_output_bytes: int = _MAX_MODEL_OUTPUT_BYTES,
    ) -> ProcessReadOutputResult:
        self._guard_execution()
        entry = await self._prepare_entry(process_id)
        if timeout_seconds > 0:
            try:
                info = await self._invoke(
                    entry,
                    lambda current: self._processes.wait(
                        current,
                        condition="tree_cleaned",
                        timeout_seconds=timeout_seconds,
                    ),
                )
                await self._update_from_info(entry, info)
            except EnvironmentError as exc:
                if exc.code != "environment_timeout":
                    await self._correct_missing(entry, exc)
                    raise
        return await self._drain(
            entry,
            wait_seconds=0,
            max_inline_bytes=max_inline_bytes,
            max_output_bytes=max_output_bytes,
        )

    async def status(self, *, cursor: int, limit: int) -> ProcessStatusListSuccess:
        self._guard_execution()
        self._require_loaded()
        candidates = [entry for process_id, entry in self._entries.items() if self._sequence(process_id) > cursor]
        selected = candidates[:limit]
        next_cursor = self._sequence(selected[-1].process_id) if len(candidates) > len(selected) and selected else None
        items: list[ProcessStatusItem] = []
        for selected_entry in selected:
            try:
                entry = await self._prepare_entry(selected_entry.process_id)
                async with entry.drain_lock:
                    if not entry.state.backend_lost:
                        info = await self._invoke(entry, self._processes.inspect)
                        await self._update_from_info(entry, info)
                        if self._mark_release_if_complete(entry, info):
                            await self._finish_release(entry, info)
                    if entry.process_id in self._entries:
                        items.append(self._status_item(entry))
            except EnvironmentError as exc:
                if exc.code == "environment_reference_invalid":
                    continue
                entry = selected_entry
                await self._correct_missing(entry, exc)
                if entry.state.backend_lost:
                    items.append(self._status_item(entry))
                else:
                    items.append(
                        {
                            "process_id": entry.process_id,
                            "ok": False,
                            "error": {"code": exc.code, "retry_hint": exc.retry_hint},
                        }
                    )
        return {
            "ok": True,
            "processes": items,
            "showing": len(items),
            "next_cursor": next_cursor,
            "truncated": next_cursor is not None,
        }

    async def write_input(self, process_id: str, data: str, *, close_stdin: bool) -> tuple[int, bool]:
        self._guard_execution()
        entry = await self._prepare_entry(process_id)
        encoded = data.encode("utf-8")
        try:
            if not encoded and close_stdin:
                await self._invoke(entry, self._processes.close_stdin)
                entry.state = entry.state.model_copy(update={"stdin_open": False})
                await self._persist()
                return 0, False
            result = await self._invoke(
                entry,
                lambda current: self._processes.write_stdin(
                    current,
                    encoded,
                    close_after_write=close_stdin,
                ),
            )
        except EnvironmentError as exc:
            await self._correct_missing(entry, exc)
            raise
        entry.state = entry.state.model_copy(update={"stdin_open": result.stdin_open})
        await self._persist()
        return result.accepted_bytes, result.stdin_open

    async def signal(
        self,
        process_id: str,
        signal: Literal["interrupt", "terminate"],
    ) -> tuple[bool, ProcessProjection]:
        self._guard_execution()
        entry = await self._prepare_entry(process_id)
        try:
            result = await self._invoke(
                entry,
                lambda current: self._processes.signal(current, signal),
            )
        except EnvironmentError as exc:
            await self._correct_missing(entry, exc)
            raise
        await self._update_from_info(entry, result.process)
        return result.accepted, self._project_process(entry, result.process)

    async def kill(
        self,
        process_id: str,
        *,
        max_inline_bytes: int = 64 * 1024,
        max_output_bytes: int = _MAX_MODEL_OUTPUT_BYTES,
    ) -> ProcessReadOutputResult:
        self._guard_execution()
        entry = await self._prepare_entry(process_id)
        try:
            result = await self._invoke(entry, self._processes.kill)
            await self._update_from_info(entry, result.process)
            try:
                info = await self._invoke(
                    entry,
                    lambda current: self._processes.wait(
                        current,
                        condition="tree_cleaned",
                        timeout_seconds=5.0,
                    ),
                )
                await self._update_from_info(entry, info)
            except EnvironmentError as exc:
                if exc.code != "environment_timeout":
                    raise
        except EnvironmentError as exc:
            await self._correct_missing(entry, exc)
            raise
        return await self._drain(
            entry,
            wait_seconds=0,
            max_inline_bytes=max_inline_bytes,
            max_output_bytes=max_output_bytes,
        )

    async def _load(self, store: AgentContextState) -> None:
        if self._loaded:
            if self._state_store is not store:
                raise RuntimeError("ProcessManager cannot cross AgentContext State coordinators")
            return
        restored = await store.read(
            PROCESS_STATE_ID,
            ProcessManagerState,
            version=_PROCESS_STATE_VERSION,
        )
        state = restored or ProcessManagerState()
        if len(state.processes) > self._max_reference_entries:
            raise EnvironmentError(
                "Restored process state exceeds the configured reference limit.",
                code="environment_reference_exhausted",
            )
        self._entries = OrderedDict(
            (process_id, _ProcessEntry(process_id=process_id, state=value))
            for process_id, value in sorted(state.processes.items(), key=lambda item: self._sequence(item[0]))
        )
        highest = max((self._sequence(process_id) for process_id in self._entries), default=0)
        self._next_sequence = max(state.next_sequence, highest + 1)
        self._state_store = store
        self._loaded = True

    async def _persist(self) -> None:
        store = self._state_store
        if store is None:
            raise RuntimeError("ProcessManager is not bound to AgentContext State")
        async with self._state_lock:
            state = ProcessManagerState(
                next_sequence=self._next_sequence,
                processes={process_id: entry.state for process_id, entry in self._entries.items()},
            )
            await store.write(PROCESS_STATE_ID, state, version=_PROCESS_STATE_VERSION)

    def _entry(self, process_id: str) -> _ProcessEntry:
        self._require_loaded()
        if _REFERENCE_PATTERN.fullmatch(process_id) is None:
            raise EnvironmentError("Expected a process reference.", code="environment_reference_invalid")
        entry = self._entries.get(process_id)
        if entry is None:
            raise EnvironmentError("Process reference is unknown.", code="environment_reference_invalid")
        return entry

    async def _prepare_entry(self, process_id: str) -> _ProcessEntry:
        entry = self._entry(process_id)
        if entry.cleanup_info is None:
            return entry
        async with entry.drain_lock:
            if entry.cleanup_info is not None and not await self._finish_release(entry, entry.cleanup_info):
                raise EnvironmentError(
                    "Process cleanup is pending.",
                    code="environment_cleanup_pending",
                    retry_hint="dependency_change",
                )
        return self._entry(process_id)

    @staticmethod
    def _sequence(process_id: str) -> int:
        match = _REFERENCE_PATTERN.fullmatch(process_id)
        if match is None:
            raise ValueError("invalid process reference")
        return int(match.group(1))

    async def _handle(self, entry: _ProcessEntry) -> BoundProcessHandle:
        if entry.state.backend_lost:
            raise EnvironmentError("The provider process no longer exists.", code="environment_reference_stale")
        if entry.handle is not None:
            return entry.handle
        try:
            info = await self._processes.rebind(entry.state.identity, output_policy=_REBIND_OUTPUT_POLICY)
        except EnvironmentError as exc:
            await self._correct_missing(entry, exc)
            raise
        await self._update_from_info(entry, info)
        return info.handle

    async def _invoke[T](
        self,
        entry: _ProcessEntry,
        operation: Callable[[BoundProcessHandle], Awaitable[T]],
    ) -> T:
        try:
            return await operation(await self._handle(entry))
        except EnvironmentError as exc:
            if exc.code == "environment_stale_mount" and entry.handle is not None:
                entry.handle = None
                try:
                    return await operation(await self._handle(entry))
                except EnvironmentError as retry_error:
                    await self._correct_missing(entry, retry_error)
                    raise
            await self._correct_missing(entry, exc)
            raise

    async def _update_from_info(self, entry: _ProcessEntry, info: ProcessInfo) -> None:
        if info.handle.identity != entry.state.identity:
            raise EnvironmentError(
                "Environment provider retargeted a managed process.",
                code="environment_provider_failure",
            )
        entry.handle = info.handle
        entry.state = entry.state.model_copy(
            update={
                "mount_id": info.handle.mount_id,
                "status": info.status,
                "stdin_open": info.stdin_open,
                "stdout_produced_bytes": info.output.stdout.produced_bytes,
                "stderr_produced_bytes": info.output.stderr.produced_bytes,
                "backend_lost": False,
            }
        )
        await self._persist()

    async def _correct_missing(self, entry: _ProcessEntry, error: EnvironmentError) -> None:
        if error.code not in {"environment_not_found", "environment_process_generation_mismatch"}:
            return
        entry.handle = None
        entry.state = entry.state.model_copy(
            update={
                "status": ProcessStatus(
                    phase="failed",
                    termination_reason="backend_lost",
                    started_at=entry.state.status.started_at,
                    ended_at=datetime.now(UTC),
                    cleanup="failed",
                ),
                "stdin_open": False,
                "backend_lost": True,
            }
        )
        await self._persist()
        await self._dispatch_event(entry, "completion")

    async def _drain(
        self,
        entry: _ProcessEntry,
        *,
        wait_seconds: float,
        max_inline_bytes: int,
        max_output_bytes: int,
    ) -> ProcessReadOutputResult:
        async with entry.drain_lock:
            aggregate_budget = min(max_inline_bytes, max_output_bytes)
            try:
                result = await self._invoke(
                    entry,
                    lambda current: self._processes.read_output(
                        current,
                        stdout_start_offset=entry.state.stdout_offset,
                        stderr_start_offset=entry.state.stderr_offset,
                        wait_seconds=wait_seconds,
                        policy=EnvironmentOutputPolicy(
                            max_inline_bytes=max(1, aggregate_budget),
                            max_output_bytes=max_output_bytes,
                            overflow="truncate",
                        ),
                    ),
                )
            except EnvironmentError as exc:
                await self._correct_missing(entry, exc)
                raise
            stdout_start = entry.state.stdout_offset
            stderr_start = entry.state.stderr_offset
            stdout_available, _ = _materialize_segments(result.stdout.chunks, stdout_start)
            stderr_available, _ = _materialize_segments(result.stderr.chunks, stderr_start)

            def project(stdout_data: bytes, stderr_data: bytes) -> dict[str, JsonValue]:
                projected: dict[str, JsonValue] = {
                    "ok": True,
                    **cast(dict[str, JsonValue], self._project_process(entry, result.process)),
                    "stdout": cast(JsonValue, _project_capture(result.stdout.capture, stdout_data)),
                    "stderr": cast(JsonValue, _project_capture(result.stderr.capture, stderr_data)),
                }
                if (
                    stdout_start + len(stdout_data) < result.stdout.capture.available_end
                    or stderr_start + len(stderr_data) < result.stderr.capture.available_end
                ):
                    projected["disclosure"] = cast(
                        JsonValue,
                        continuation_disclosure(
                            projected,
                            hint=(
                                f"Call shell_wait with process_id={entry.process_id!r} and "
                                "timeout_seconds=0 to read the next output page."
                            ),
                        ),
                    )
                return projected

            stdout_data, stderr_data, projected = _fit_stream_projection(
                stdout_available,
                stderr_available,
                raw_budget=aggregate_budget,
                json_budget=DEFAULT_TOOL_OUTPUT_CHARS - _PROCESS_RESULT_ENVELOPE_CHARS,
                project=project,
            )
            _require_stream_progress(stdout_available, stderr_available, stdout_data, stderr_data)
            entry.state = entry.state.model_copy(
                update={
                    "mount_id": result.process.handle.mount_id,
                    "stdout_offset": stdout_start + len(stdout_data),
                    "stderr_offset": stderr_start + len(stderr_data),
                    "status": result.process.status,
                    "stdin_open": result.process.stdin_open,
                    "stdout_produced_bytes": result.process.output.stdout.produced_bytes,
                    "stderr_produced_bytes": result.process.output.stderr.produced_bytes,
                }
            )
            await self._persist()
            self._mark_release_if_complete(entry, result.process)
            return cast(ProcessReadOutputResult, acknowledge_tool_output(projected))

    def _project_process(
        self,
        entry: _ProcessEntry,
        process: ProcessInfo,
        *,
        consume_output: bool = False,
        output_budget: int = _MAX_MODEL_TEXT_BYTES,
    ) -> ProcessProjection:
        stdout_available = _capture_initial_bytes(process.output.stdout) if consume_output else b""
        stderr_available = _capture_initial_bytes(process.output.stderr) if consume_output else b""
        stdout_start = entry.state.stdout_offset
        stderr_start = entry.state.stderr_offset

        def project(stdout_data: bytes, stderr_data: bytes) -> ProcessProjection:
            projected: ProcessProjection = {
                "process_id": entry.process_id,
                "status": _project_status(process.status),
                "stdin_open": process.stdin_open,
                "stdout": _project_capture(process.output.stdout, stdout_data),
                "stderr": _project_capture(process.output.stderr, stderr_data),
            }
            if consume_output and (
                stdout_start + len(stdout_data) < process.output.stdout.available_end
                or stderr_start + len(stderr_data) < process.output.stderr.available_end
            ):
                projected["disclosure"] = continuation_disclosure(
                    cast(dict[str, JsonValue], projected),
                    hint=(
                        f"Call shell_wait with process_id={entry.process_id!r} and "
                        "timeout_seconds=0 to read the next output page."
                    ),
                )
            return projected

        if not consume_output:
            return project(b"", b"")
        stdout_data, stderr_data, projected = _fit_stream_projection(
            stdout_available,
            stderr_available,
            raw_budget=output_budget,
            json_budget=DEFAULT_TOOL_OUTPUT_CHARS - _PROCESS_RESULT_ENVELOPE_CHARS,
            project=project,
        )
        entry.state = entry.state.model_copy(
            update={
                "stdout_offset": stdout_start + len(stdout_data),
                "stderr_offset": stderr_start + len(stderr_data),
            }
        )
        return projected

    def _status_item(self, entry: _ProcessEntry) -> ProcessStatusItem:
        return {
            "process_id": entry.process_id,
            "ok": True,
            "status": _project_status(entry.state.status),
            "stdin_open": entry.state.stdin_open,
            "produced_bytes": {
                "stdout": entry.state.stdout_produced_bytes,
                "stderr": entry.state.stderr_produced_bytes,
            },
        }

    async def _cancel_observers(self) -> None:
        tasks = tuple(
            entry.observer
            for entry in self._entries.values()
            if entry.observer is not None and not entry.observer.done()
        )
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for entry in self._entries.values():
            entry.observer = None

    def _ensure_observer(self, entry: _ProcessEntry) -> None:
        if (
            self._closed
            or self._active_context is None
            or entry.state.backend_lost
            or entry.state.status.phase in _TERMINAL_PHASES
            or (entry.observer is not None and not entry.observer.done())
        ):
            return
        entry.observer = asyncio.create_task(self._observe(entry))

    async def _observe(self, entry: _ProcessEntry) -> None:
        try:
            while True:
                try:
                    info = await self._invoke(
                        entry,
                        lambda current: self._processes.wait(
                            current,
                            condition="tree_cleaned",
                            timeout_seconds=3600.0,
                        ),
                    )
                except EnvironmentError as exc:
                    if exc.code == "environment_timeout":
                        continue
                    await self._correct_missing(entry, exc)
                    if not entry.state.backend_lost:
                        await self._dispatch_event(entry, "gap")
                    return
                await self._update_from_info(entry, info)
                await self._dispatch_event(entry, "completion")
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._dispatch_event(entry, "gap")

    async def _dispatch_event(self, entry: _ProcessEntry, kind: ProcessEventKind) -> None:
        previous = self._notified.get(entry.process_id)
        if previous == "completion" or previous == kind:
            return
        self._notified[entry.process_id] = kind
        event_context = self._event_context
        if event_context is None:
            raise RuntimeError("ProcessManager is not bound to an AgentContext")
        event = ProcessEvent(
            kind=kind,
            thread_id=event_context[0],
            run_id=event_context[1],
            agent_instance_id=event_context[2],
            process_id=entry.process_id,
            identity=entry.state.identity,
            status=entry.state.status,
        )
        self._pending[entry.process_id] = kind
        self._pending.move_to_end(entry.process_id)
        self._flush_notifications()
        for hook in self._event_hooks:
            try:
                await hook(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                continue

    def _flush_notifications(self) -> None:
        active = self._active_context
        if active is None or not self._pending:
            return
        pending = tuple(self._pending.items())
        shown = pending[:_MAX_NOTIFICATION_ITEMS]
        lines = ["Background process update:"]
        for process_id, kind in shown:
            if kind == "completion":
                lines.append(f"- {process_id} completed; call shell_wait with timeout_seconds=0 for final output.")
            else:
                lines.append(f"- {process_id} completion observation is unavailable; use shell_status or shell_wait.")
        if len(pending) > len(shown):
            lines.append(f"- {len(pending) - len(shown)} more process updates are available through shell_status.")
        enqueue_id = active.enqueue("\n".join(lines), priority="asap")
        if enqueue_id is not None:
            self._pending.clear()

    def _mark_release_if_complete(self, entry: _ProcessEntry, info: ProcessInfo) -> bool:
        complete = (
            info.status.phase in _TERMINAL_PHASES
            and info.status.cleanup != "pending"
            and entry.state.stdout_offset >= info.output.stdout.available_end
            and entry.state.stderr_offset >= info.output.stderr.available_end
        )
        if complete:
            entry.cleanup_info = info
        return complete

    async def _finish_release(self, entry: _ProcessEntry, info: ProcessInfo) -> bool:
        entry.cleanup_info = info
        cleanup = asyncio.create_task(self._complete_release(entry, info))
        error, cancellation = await _await_owned_cleanup(cleanup)
        if cancellation is not None:
            raise cancellation
        return error is None

    async def _complete_release(self, entry: _ProcessEntry, info: ProcessInfo) -> None:
        await self._release_resources(info)
        observer = entry.observer
        if observer is not None and observer is not asyncio.current_task() and not observer.done():
            observer.cancel()
            await asyncio.gather(observer, return_exceptions=True)
        entry.observer = None
        self._entries.pop(entry.process_id, None)
        try:
            await self._persist()
        except BaseException:
            self._entries[entry.process_id] = entry
            raise

    async def _release_resources(self, info: ProcessInfo) -> None:
        try:
            await self._processes.release(info.handle)
        except EnvironmentError as exc:
            if exc.code != "environment_not_found":
                raise
        for capture in (info.output.stdout, info.output.stderr):
            if capture.reference is None:
                continue
            try:
                await self._outputs.release(reference=capture.reference)
            except EnvironmentError as exc:
                if exc.code != "environment_not_found":
                    raise

    async def _cleanup_unexposed(self, info: ProcessInfo) -> None:
        try:
            await self._processes.kill(info.handle)
        except Exception:
            pass
        try:
            await self._processes.wait(info.handle, condition="tree_cleaned", timeout_seconds=5.0)
        except Exception:
            pass
        cleanup = asyncio.create_task(self._release_resources(info))
        await _await_owned_cleanup(cleanup)

    @staticmethod
    def _state_from_info(info: ProcessInfo) -> ManagedProcessState:
        return ManagedProcessState(
            identity=info.handle.identity,
            mount_id=info.handle.mount_id,
            status=info.status,
            stdin_open=info.stdin_open,
            stdout_produced_bytes=info.output.stdout.produced_bytes,
            stderr_produced_bytes=info.output.stderr.produced_bytes,
        )

    def _require_loaded(self) -> None:
        if not self._loaded:
            raise RuntimeError("ProcessManager must be entered through ShellToolset.wrap_run")

    def _guard_execution(self) -> None:
        if self._execution_guard is not None:
            self._execution_guard()


def _project_status(status: ProcessStatus) -> ProcessStatusProjection:
    return {
        "phase": status.phase,
        "termination_reason": status.termination_reason,
        "exit_code": status.exit_code,
        "signal": status.signal,
        "cleanup": status.cleanup,
    }


def _project_capture(capture: EnvironmentOutputCapture, data: bytes = b"") -> OutputCaptureProjection:
    return {
        "kind": capture.kind,
        "producer_complete": capture.producer_complete,
        "content_complete": capture.content_complete and len(data) == capture.captured_bytes,
        "produced_bytes": capture.produced_bytes,
        "captured_bytes": len(data),
        "dropped_bytes": capture.dropped_bytes,
        "text": data.decode("utf-8", errors="replace"),
        "available_start": capture.available_start,
        "available_end": capture.available_end,
    }


def _fit_stream_prefixes(stdout: bytes, stderr: bytes, budget: int) -> tuple[bytes, bytes]:
    """Allocate one aggregate contiguous-prefix budget across two output streams."""
    if budget <= 0:
        return b"", b""
    stdout_limit = (budget + 1) // 2
    stderr_limit = budget // 2
    selected_stdout = stdout[:stdout_limit]
    selected_stderr = stderr[:stderr_limit]
    remaining = budget - len(selected_stdout) - len(selected_stderr)
    if remaining > 0:
        extra_stdout = stdout[len(selected_stdout) : len(selected_stdout) + remaining]
        selected_stdout += extra_stdout
        remaining -= len(extra_stdout)
    if remaining > 0:
        selected_stderr += stderr[len(selected_stderr) : len(selected_stderr) + remaining]
    return _utf8_safe_prefix(selected_stdout, stdout), _utf8_safe_prefix(selected_stderr, stderr)


def _utf8_safe_prefix(value: bytes, available: bytes) -> bytes:
    if not value or len(value) >= len(available):
        return value
    end = len(value)
    start = end - 1
    while start >= 0 and end - start <= 4 and value[start] & 0xC0 == 0x80:
        start -= 1
    if start < 0:
        return b""
    lead = value[start]
    expected = 1
    if 0xC2 <= lead <= 0xDF:
        expected = 2
    elif 0xE0 <= lead <= 0xEF:
        expected = 3
    elif 0xF0 <= lead <= 0xF4:
        expected = 4
    if expected > 1 and end - start < expected:
        sequence = available[start : start + expected]
        try:
            sequence.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return value
        return value[:start]
    return value


def _fit_stream_projection[ProjectionT](
    stdout: bytes,
    stderr: bytes,
    *,
    raw_budget: int,
    json_budget: int,
    project: Callable[[bytes, bytes], ProjectionT],
) -> tuple[bytes, bytes, ProjectionT]:
    selected_stdout, selected_stderr = _fit_stream_prefixes(stdout, stderr, 0)
    selected_projection = project(selected_stdout, selected_stderr)
    high = min(max(raw_budget, 0), len(stdout) + len(stderr))
    low = 1
    while low <= high:
        candidate_budget = (low + high) // 2
        candidate_stdout, candidate_stderr = _fit_stream_prefixes(stdout, stderr, candidate_budget)
        candidate_projection = project(candidate_stdout, candidate_stderr)
        safe_projection = redact_json(cast(JsonValue, candidate_projection))
        assert isinstance(safe_projection, dict)
        if tool_output_size(safe_projection) <= json_budget:
            selected_stdout = candidate_stdout
            selected_stderr = candidate_stderr
            selected_projection = candidate_projection
            low = candidate_budget + 1
        else:
            high = candidate_budget - 1
    return selected_stdout, selected_stderr, selected_projection


def _capture_initial_bytes(capture: EnvironmentOutputCapture) -> bytes:
    if capture.inline is not None:
        return capture.inline
    if not capture.preview:
        return b""
    segments = sorted(capture.preview, key=lambda segment: segment.start_offset)
    expected = 0
    chunks: list[bytes] = []
    for segment in segments:
        if segment.start_offset != expected:
            break
        chunks.append(segment.data)
        expected += len(segment.data)
    return b"".join(chunks)


def _materialize_segments(segments: Sequence[Any], start_offset: int) -> tuple[bytes, int]:
    expected = start_offset
    chunks: list[bytes] = []
    for segment in sorted(segments, key=lambda item: item.start_offset):
        if segment.start_offset != expected:
            raise EnvironmentError("Process output contains a gap.", code="environment_output_gap")
        chunks.append(segment.data)
        expected += len(segment.data)
    return b"".join(chunks), expected


def _require_stream_progress(
    stdout_available: bytes,
    stderr_available: bytes,
    stdout_selected: bytes,
    stderr_selected: bytes,
) -> None:
    if (stdout_available or stderr_available) and not (stdout_selected or stderr_selected):
        raise EnvironmentError(
            "The output page byte limit cannot contain the next complete UTF-8 character.",
            code="environment_too_large",
        )


async def _await_owned_cleanup(
    task: asyncio.Task[None],
) -> tuple[BaseException | None, asyncio.CancelledError | None]:
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if cancellation is None:
                cancellation = exc
        except BaseException:
            break
    if task.cancelled():
        return asyncio.CancelledError("Process cleanup task was cancelled."), cancellation
    return task.exception(), cancellation


__all__ = [
    "PROCESS_STATE_ID",
    "ManagedProcessState",
    "ProcessEvent",
    "ProcessEventHook",
    "ProcessEventKind",
    "ProcessManager",
    "ProcessManagerState",
]
