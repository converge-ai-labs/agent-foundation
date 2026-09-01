"""Portable Agent process projections over fresh Host-managed operations."""

from __future__ import annotations

import asyncio
import re
from collections import OrderedDict
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_serializer, field_validator
from pydantic_ai import RunContext

from a13n_harness._json import redact_json
from a13n_harness.capabilities.processes import (
    ProcessBackendEventHook,
    ProcessExecutionSnapshot,
    ProcessOutputChunk,
    ProcessOutputPage,
    ShellOperator,
)
from a13n_harness.context import AgentContext
from a13n_harness.environment.commands import CommandRequest, ProcessStatus
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.retention import EnvironmentOutputCapture
from a13n_harness.state import AgentContextState

from .output import DEFAULT_TOOL_OUTPUT_CHARS, acknowledge_tool_output, continuation_disclosure, tool_output_size
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
_PROCESS_STATE_VERSION = "2"
PROCESS_STATE_ID = "a13n.dynamic-environment.processes"


class ManagedProcessState(BaseModel):
    """Portable parent-private projection of one Host-managed process."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    backend_id: str = Field(min_length=1, max_length=512)
    stdout_offset: int = Field(default=0, ge=0)
    stderr_offset: int = Field(default=0, ge=0)
    status: ProcessStatus
    stdin_open: bool
    stdout_produced_bytes: int = Field(default=0, ge=0)
    stderr_produced_bytes: int = Field(default=0, ge=0)
    backend_lost: bool = False


class ProcessManagerState(BaseModel):
    """Versioned process-N mapping stored in the current Agent context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_thread_id: str = Field(min_length=1, max_length=256)
    next_sequence: int = Field(default=1, ge=1, le=_MAX_REFERENCE_ENTRIES + 1)
    processes: Mapping[str, ManagedProcessState] = Field(default_factory=dict)

    @field_validator("processes", mode="after")
    @classmethod
    def _validate_processes(cls, value: Mapping[str, ManagedProcessState]) -> Mapping[str, ManagedProcessState]:
        copied = dict(value)
        if len(copied) > _MAX_REFERENCE_ENTRIES:
            raise ValueError("process state exceeds the finite reference limit")
        if any(_REFERENCE_PATTERN.fullmatch(process_id) is None for process_id in copied):
            raise ValueError("process state contains an invalid compact reference")
        return MappingProxyType(copied)

    @field_serializer("processes")
    def _serialize_processes(self, value: Mapping[str, ManagedProcessState]) -> dict[str, ManagedProcessState]:
        return dict(value)


@dataclass(slots=True)
class _ProcessEntry:
    process_id: str
    state: ManagedProcessState
    drain_lock: asyncio.Lock = dataclass_field(default_factory=asyncio.Lock)


class _ProcessRunManager:
    """Manage one parent Run's compact process projection over a stable operator."""

    def __init__(
        self,
        *,
        operator: ShellOperator,
        execution_guard: Callable[[], None] | None = None,
    ) -> None:
        if not isinstance(operator, ShellOperator):
            raise TypeError("operator must be a ShellOperator")
        self._operator = operator
        self._execution_guard = execution_guard
        self._entries: OrderedDict[str, _ProcessEntry] = OrderedDict()
        self._next_sequence = 1
        self._state_store: AgentContextState | None = None
        self._owner_thread_id: str | None = None
        self._state_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._active_context: RunContext[AgentContext] | None = None
        self._loaded = False
        self._observers: dict[str, ProcessBackendEventHook] = {}

    @asynccontextmanager
    async def active_run(self, ctx: RunContext[AgentContext]) -> AsyncGenerator[None]:
        """Bind parent state and reconcile canonical Host snapshots for this run."""
        await self._load(ctx.deps.state, owner_thread_id=ctx.deps.thread_id)
        owned = self._active_context is None
        if owned:
            self._active_context = ctx
            await self._attach_nonterminal()
        try:
            yield
        finally:
            if owned and self._active_context is ctx:
                self._active_context = None

    async def start(self, request: CommandRequest, *, alias: str | None) -> ProcessProjection:
        self._guard_execution()
        self._require_loaded()
        async with self._start_lock:
            if len(self._entries) >= _MAX_REFERENCE_ENTRIES or self._next_sequence > _MAX_REFERENCE_ENTRIES:
                raise EnvironmentError(
                    "Environment compact reference capacity is exhausted.",
                    code="environment_reference_exhausted",
                )
            process_id = f"process-{self._next_sequence}"
            observer = self._event_callback(process_id)
            self._observers[process_id] = observer
            snapshot: ProcessExecutionSnapshot | None = None
            try:
                snapshot = await self._operator.start(self._context(), request, alias, process_id, observer)
                _validate_snapshot(snapshot, snapshot.backend_id)
                entry = _ProcessEntry(process_id=process_id, state=_state_from_snapshot(snapshot))
                self._entries[process_id] = entry
                self._next_sequence += 1
                try:
                    await self._persist()
                except BaseException:
                    self._entries.pop(process_id, None)
                    self._next_sequence -= 1
                    raise
            except BaseException:
                self._observers.pop(process_id, None)
                if snapshot is not None:
                    await _kill_accepted_process(self._operator, self._context(), snapshot.backend_id)
                raise
        return self._project_process(entry)

    def resource_id(self, process_id: str) -> str:
        return self._entry(process_id).state.backend_id

    async def wait(
        self,
        process_id: str,
        *,
        timeout_seconds: float,
        max_inline_bytes: int = 64 * 1024,
        max_output_bytes: int = _MAX_MODEL_OUTPUT_BYTES,
    ) -> ProcessReadOutputResult:
        self._guard_execution()
        entry = self._entry(process_id)
        return await self._drain(
            entry,
            wait_seconds=timeout_seconds,
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
        for entry in selected:
            if not entry.state.backend_lost and entry.state.status.phase not in _TERMINAL_PHASES:
                snapshot = await self._operator.inspect(self._context(), entry.state.backend_id)
                if snapshot is None:
                    await self._mark_lost(entry)
                else:
                    await self._apply_snapshot(entry, snapshot)
            items.append(self._status_item(entry))
        return {
            "ok": True,
            "processes": items,
            "showing": len(items),
            "next_cursor": next_cursor,
            "truncated": next_cursor is not None,
        }

    async def write_input(self, process_id: str, data: str, *, close_stdin: bool) -> tuple[int, bool]:
        self._guard_execution()
        entry = self._entry(process_id)
        self._require_live(entry)
        result = await self._operator.write_stdin(
            self._context(),
            entry.state.backend_id,
            data.encode("utf-8"),
            close_stdin,
        )
        if result is None:
            await self._mark_lost(entry)
            raise EnvironmentError("The Host process no longer exists.", code="environment_reference_stale")
        _validate_snapshot(result.snapshot, entry.state.backend_id)
        await self._apply_snapshot(entry, result.snapshot)
        return result.accepted_bytes, result.snapshot.stdin_open

    async def signal(
        self,
        process_id: str,
        signal: Literal["interrupt", "terminate"],
    ) -> tuple[bool, ProcessProjection]:
        self._guard_execution()
        entry = self._entry(process_id)
        self._require_live(entry)
        result = await self._operator.signal(self._context(), entry.state.backend_id, signal)
        if result is None:
            await self._mark_lost(entry)
            raise EnvironmentError("The Host process no longer exists.", code="environment_reference_stale")
        _validate_snapshot(result.snapshot, entry.state.backend_id)
        await self._apply_snapshot(entry, result.snapshot)
        return result.accepted, self._project_process(entry)

    async def kill(
        self,
        process_id: str,
        *,
        max_inline_bytes: int = 64 * 1024,
        max_output_bytes: int = _MAX_MODEL_OUTPUT_BYTES,
    ) -> ProcessReadOutputResult:
        self._guard_execution()
        entry = self._entry(process_id)
        self._require_live(entry)
        snapshot = await self._operator.kill(self._context(), entry.state.backend_id)
        if snapshot is None:
            await self._mark_lost(entry)
            raise EnvironmentError("The Host process no longer exists.", code="environment_reference_stale")
        await self._apply_snapshot(entry, snapshot)
        return await self._drain(
            entry,
            wait_seconds=0,
            max_inline_bytes=max_inline_bytes,
            max_output_bytes=max_output_bytes,
        )

    async def _load(self, store: AgentContextState, *, owner_thread_id: str) -> None:
        if self._loaded:
            if self._state_store is not store or self._owner_thread_id != owner_thread_id:
                raise RuntimeError("Process run projection cannot cross Agent Contexts")
            return
        restored = await store.read(PROCESS_STATE_ID, ProcessManagerState, version=_PROCESS_STATE_VERSION)
        if restored is None:
            state = ProcessManagerState(owner_thread_id=owner_thread_id)
        elif restored.owner_thread_id != owner_thread_id:
            state = ProcessManagerState(
                owner_thread_id=owner_thread_id,
                next_sequence=restored.next_sequence,
            )
        else:
            state = restored
        self._entries = OrderedDict(
            (process_id, _ProcessEntry(process_id=process_id, state=value))
            for process_id, value in sorted(state.processes.items(), key=lambda item: self._sequence(item[0]))
        )
        highest = max((self._sequence(process_id) for process_id in self._entries), default=0)
        self._next_sequence = max(state.next_sequence, highest + 1)
        self._state_store = store
        self._owner_thread_id = owner_thread_id
        self._loaded = True
        if restored is not None and restored.owner_thread_id != owner_thread_id:
            await self._persist()

    async def _attach_nonterminal(self) -> None:
        for entry in tuple(self._entries.values()):
            if entry.state.backend_lost or entry.state.status.phase in _TERMINAL_PHASES:
                continue
            try:
                observer = self._event_callback(entry.process_id)
                self._observers[entry.process_id] = observer
                snapshot = await self._operator.rebind(
                    self._context(),
                    entry.state.backend_id,
                    observer,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._notify(entry, "gap")
                continue
            if snapshot is None:
                await self._mark_lost(entry)
            else:
                await self._apply_snapshot(entry, snapshot)

    async def _persist(self) -> None:
        store = self._state_store
        if store is None:
            raise RuntimeError("ProcessManager is not bound to AgentContext State")
        async with self._state_lock:
            owner_thread_id = self._owner_thread_id
            if owner_thread_id is None:
                raise RuntimeError("Process run projection has no owner Thread")
            await store.write(
                PROCESS_STATE_ID,
                ProcessManagerState(
                    owner_thread_id=owner_thread_id,
                    next_sequence=self._next_sequence,
                    processes={process_id: entry.state for process_id, entry in self._entries.items()},
                ),
                version=_PROCESS_STATE_VERSION,
            )

    def _entry(self, process_id: str) -> _ProcessEntry:
        self._require_loaded()
        if _REFERENCE_PATTERN.fullmatch(process_id) is None:
            raise EnvironmentError("Expected a process reference.", code="environment_reference_invalid")
        entry = self._entries.get(process_id)
        if entry is None:
            raise EnvironmentError("Process reference is unknown.", code="environment_reference_invalid")
        return entry

    @staticmethod
    def _sequence(process_id: str) -> int:
        match = _REFERENCE_PATTERN.fullmatch(process_id)
        if match is None:
            raise ValueError("invalid process reference")
        return int(match.group(1))

    def _event_callback(self, process_id: str) -> Callable[[ProcessExecutionSnapshot], Awaitable[None]]:
        async def callback(snapshot: ProcessExecutionSnapshot) -> None:
            entry = self._entries.get(process_id)
            if entry is None:
                return
            _validate_snapshot(snapshot, entry.state.backend_id)
            if self._active_context is not None:
                await self._apply_snapshot(entry, snapshot)

        return callback

    async def _apply_snapshot(self, entry: _ProcessEntry, snapshot: ProcessExecutionSnapshot) -> None:
        _validate_snapshot(snapshot, entry.state.backend_id)
        previous = entry.state.status.phase
        if previous in _TERMINAL_PHASES:
            return
        entry.state = _merge_snapshot(entry.state, snapshot)
        await self._persist()
        if entry.state.status.phase in _TERMINAL_PHASES:
            await self._notify(entry, "completion")

    async def _mark_lost(self, entry: _ProcessEntry) -> None:
        if entry.state.backend_lost or entry.state.status.phase in _TERMINAL_PHASES:
            return
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
        await self._notify(entry, "gap")

    async def _drain(
        self,
        entry: _ProcessEntry,
        *,
        wait_seconds: float,
        max_inline_bytes: int,
        max_output_bytes: int,
    ) -> ProcessReadOutputResult:
        async with entry.drain_lock:
            return await self._drain_unlocked(
                entry,
                wait_seconds=wait_seconds,
                max_inline_bytes=max_inline_bytes,
                max_output_bytes=max_output_bytes,
            )

    async def _drain_unlocked(
        self,
        entry: _ProcessEntry,
        *,
        wait_seconds: float,
        max_inline_bytes: int,
        max_output_bytes: int,
    ) -> ProcessReadOutputResult:
        self._require_live(entry, allow_terminal=True)
        aggregate_budget = min(max_inline_bytes, max_output_bytes)
        page_budget = max(1, aggregate_budget)
        stdout_offset = entry.state.stdout_offset
        stderr_offset = entry.state.stderr_offset
        stdout_produced_bytes = entry.state.stdout_produced_bytes
        stderr_produced_bytes = entry.state.stderr_produced_bytes
        page = await self._operator.read_output(
            self._context(),
            entry.state.backend_id,
            stdout_offset,
            stderr_offset,
            wait_seconds,
            page_budget,
        )
        if page is None:
            await self._mark_lost(entry)
            raise EnvironmentError("The Host process no longer exists.", code="environment_reference_stale")
        _validate_output_page(
            page,
            entry.state.backend_id,
            stdout_offset=stdout_offset,
            stderr_offset=stderr_offset,
            max_bytes=page_budget,
            minimum_stdout_produced_bytes=stdout_produced_bytes,
            minimum_stderr_produced_bytes=stderr_produced_bytes,
        )
        stdout_available = page.stdout.data
        stderr_available = page.stderr.data
        merged_snapshot = _snapshot_from_state(_merge_snapshot(entry.state, page.snapshot))
        producer_complete = merged_snapshot.status.phase in _TERMINAL_PHASES

        def project(stdout_data: bytes, stderr_data: bytes) -> dict[str, JsonValue]:
            projected: dict[str, JsonValue] = {
                "ok": True,
                **cast(dict[str, JsonValue], self._project_process(entry, snapshot=merged_snapshot)),
                "stdout": cast(
                    JsonValue,
                    _project_chunk(
                        page.stdout,
                        merged_snapshot.stdout_produced_bytes,
                        stdout_data,
                        producer_complete=producer_complete,
                    ),
                ),
                "stderr": cast(
                    JsonValue,
                    _project_chunk(
                        page.stderr,
                        merged_snapshot.stderr_produced_bytes,
                        stderr_data,
                        producer_complete=producer_complete,
                    ),
                ),
            }
            if len(stdout_data) < len(stdout_available) or len(stderr_data) < len(stderr_available):
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
        previous = entry.state.status.phase
        entry.state = _state_from_snapshot(merged_snapshot).model_copy(
            update={
                "stdout_offset": page.stdout.start_offset + len(stdout_data),
                "stderr_offset": page.stderr.start_offset + len(stderr_data),
            }
        )
        await self._persist()
        if previous not in _TERMINAL_PHASES and entry.state.status.phase in _TERMINAL_PHASES:
            await self._notify(entry, "completion")
        return cast(ProcessReadOutputResult, acknowledge_tool_output(projected))

    def _project_process(
        self,
        entry: _ProcessEntry,
        *,
        snapshot: ProcessExecutionSnapshot | None = None,
    ) -> ProcessProjection:
        current = snapshot or _snapshot_from_state(entry.state)
        return {
            "process_id": entry.process_id,
            "status": _project_status(current.status),
            "stdin_open": current.stdin_open,
            "stdout": _empty_capture(current.stdout_produced_bytes, current.status.phase in _TERMINAL_PHASES),
            "stderr": _empty_capture(current.stderr_produced_bytes, current.status.phase in _TERMINAL_PHASES),
        }

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

    async def _notify(self, entry: _ProcessEntry, kind: Literal["completion", "gap"]) -> None:
        active = self._active_context
        if active is None:
            return
        if kind == "completion":
            message = (
                f"Background process {entry.process_id} has finished. "
                f"Call shell_wait with process_id={entry.process_id!r} and timeout_seconds=0 "
                "to collect its final output."
            )
        else:
            message = (
                f"Background process {entry.process_id} can no longer be observed by this backend. "
                "Call shell_status to inspect its retained status."
            )
        await active.deps._steering.notify(
            message,
            source="background_process",
            references=(entry.process_id,),
        )

    def _context(self) -> AgentContext:
        active = self._active_context
        if active is None:
            raise RuntimeError("Process operation requires an active Harness Run")
        return active.deps

    def _require_loaded(self) -> None:
        if not self._loaded:
            raise RuntimeError("Process run projection must be entered through ShellToolset.wrap_run")

    def _require_live(self, entry: _ProcessEntry, *, allow_terminal: bool = False) -> None:
        if entry.state.backend_lost:
            raise EnvironmentError("The Host process no longer exists.", code="environment_reference_stale")
        if not allow_terminal and entry.state.status.phase in _TERMINAL_PHASES:
            raise EnvironmentError("The Host process is not running.", code="environment_reference_stale")

    def _guard_execution(self) -> None:
        if self._execution_guard is not None:
            self._execution_guard()


def _state_from_snapshot(snapshot: ProcessExecutionSnapshot) -> ManagedProcessState:
    return ManagedProcessState(
        backend_id=snapshot.backend_id,
        status=snapshot.status,
        stdin_open=snapshot.stdin_open,
        stdout_produced_bytes=snapshot.stdout_produced_bytes,
        stderr_produced_bytes=snapshot.stderr_produced_bytes,
    )


def _snapshot_from_state(state: ManagedProcessState) -> ProcessExecutionSnapshot:
    return ProcessExecutionSnapshot(
        backend_id=state.backend_id,
        status=state.status,
        stdin_open=state.stdin_open,
        stdout_produced_bytes=state.stdout_produced_bytes,
        stderr_produced_bytes=state.stderr_produced_bytes,
    )


def _merge_snapshot(state: ManagedProcessState, snapshot: ProcessExecutionSnapshot) -> ManagedProcessState:
    status = state.status if state.status.phase in _TERMINAL_PHASES else snapshot.status
    stdin_open = state.stdin_open if state.status.phase in _TERMINAL_PHASES else snapshot.stdin_open
    return state.model_copy(
        update={
            "status": status,
            "stdin_open": stdin_open,
            "stdout_produced_bytes": max(state.stdout_produced_bytes, snapshot.stdout_produced_bytes),
            "stderr_produced_bytes": max(state.stderr_produced_bytes, snapshot.stderr_produced_bytes),
            "backend_lost": False,
        }
    )


def _validate_snapshot(snapshot: ProcessExecutionSnapshot, backend_id: str) -> None:
    if not isinstance(snapshot, ProcessExecutionSnapshot):
        raise TypeError("process backend returned an invalid snapshot")
    if snapshot.backend_id != backend_id:
        raise EnvironmentError("Host backend retargeted a managed process.", code="environment_provider_failure")


def _validate_output_page(
    page: ProcessOutputPage,
    backend_id: str,
    *,
    stdout_offset: int,
    stderr_offset: int,
    max_bytes: int,
    minimum_stdout_produced_bytes: int,
    minimum_stderr_produced_bytes: int,
) -> None:
    if not isinstance(page, ProcessOutputPage):
        raise TypeError("process backend returned an invalid output page")
    _validate_snapshot(page.snapshot, backend_id)
    if (
        page.snapshot.stdout_produced_bytes < minimum_stdout_produced_bytes
        or page.snapshot.stderr_produced_bytes < minimum_stderr_produced_bytes
    ):
        raise EnvironmentError("Host process output counters moved backwards.", code="environment_provider_failure")
    if len(page.stdout.data) + len(page.stderr.data) > max_bytes:
        raise EnvironmentError("Host process exceeded the output page budget.", code="environment_provider_failure")
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
            "Host process returned inconsistent output offsets.", code="environment_provider_failure"
        )
    expected_start = max(requested_offset, chunk.available_start)
    if chunk.start_offset != expected_start:
        raise EnvironmentError(
            "Host process returned a noncontiguous output page.", code="environment_provider_failure"
        )


async def _kill_accepted_process(
    operator: ShellOperator,
    context: AgentContext,
    backend_id: str,
) -> None:
    task = asyncio.create_task(operator.kill(context, backend_id))
    cancelled = False
    while True:
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                break
            cancelled = True
            continue
        except Exception:
            break
        break
    if cancelled:
        raise asyncio.CancelledError


def _project_status(status: ProcessStatus) -> ProcessStatusProjection:
    return {
        "phase": status.phase,
        "termination_reason": status.termination_reason,
        "exit_code": status.exit_code,
        "signal": status.signal,
        "cleanup": status.cleanup,
    }


def _empty_capture(produced_bytes: int, producer_complete: bool) -> OutputCaptureProjection:
    return {
        "kind": "empty" if produced_bytes == 0 else "retained",
        "producer_complete": producer_complete,
        "content_complete": produced_bytes == 0,
        "produced_bytes": produced_bytes,
        "captured_bytes": 0,
        "dropped_bytes": 0,
        "text": "",
        "available_start": 0,
        "available_end": produced_bytes,
    }


def _project_chunk(
    chunk: ProcessOutputChunk,
    produced_bytes: int,
    data: bytes,
    *,
    producer_complete: bool = False,
) -> OutputCaptureProjection:
    return {
        "kind": "empty" if produced_bytes == 0 else "retained",
        "producer_complete": producer_complete or chunk.producer_complete,
        "content_complete": chunk.content_complete and len(data) == chunk.available_end - chunk.start_offset,
        "produced_bytes": produced_bytes,
        "captured_bytes": len(data),
        "dropped_bytes": chunk.available_start,
        "text": data.decode("utf-8", errors="replace"),
        "available_start": chunk.available_start,
        "available_end": chunk.available_end,
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


__all__ = [
    "PROCESS_STATE_ID",
    "ManagedProcessState",
    "ProcessManagerState",
]
