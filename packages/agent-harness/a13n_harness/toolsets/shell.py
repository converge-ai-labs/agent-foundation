"""Reusable model-facing shell, process, output, and port Toolset."""

from __future__ import annotations

import asyncio
import inspect
import re
import threading
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Protocol, cast

from pydantic import Field, JsonValue
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness._json import redact_json
from a13n_harness.context import AgentContext
from a13n_harness.environment.commands import (
    ArgvCommand,
    CommandEnvironment,
    CommandLimits,
    CommandRequest,
    PortObservation,
    PortTarget,
    ProcessInfo,
    ProcessStatus,
    ShellCommand,
)
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.providers import (
    BoundOutputOperations,
    BoundPortOperations,
    BoundProcessOperations,
    BoundShellOperations,
)
from a13n_harness.environment.retention import (
    EnvironmentOutputCapture,
    EnvironmentOutputPolicy,
)
from a13n_harness.errors import DefinitionError
from a13n_harness.tools.metadata import (
    HarnessTool,
    HarnessToolMetadata,
    ToolEffect,
    ToolOutputPolicy,
    ToolResourceResolver,
)

from ._results import ToolFailure
from .output import (
    DEFAULT_TOOL_OUTPUT_CHARS,
    acknowledge_tool_output,
    continuation_disclosure,
    disclose_sequence_field,
    disclose_text_paths,
    tool_output_size,
)
from .shell_results import (
    OutputCaptureProjection,
    PortProjection,
    PortToolResult,
    ProcessCloseStdinResult,
    ProcessProjection,
    ProcessReadOutputResult,
    ProcessSignalResult,
    ProcessStatusItem,
    ProcessStatusListResult,
    ProcessStatusProjection,
    ProcessToolResult,
    ProcessWriteStdinResult,
    ReleaseResult,
    ShellExecToolResult,
)

_MAX_MODEL_TEXT_BYTES = 256 * 1024
_MAX_MODEL_OUTPUT_BYTES = 1024 * 1024
_MAX_MODEL_RESULTS = 1_000
_PROCESS_RESULT_ENVELOPE_CHARS = 128
_MAX_REFERENCE_ENTRIES = 100_000
_REFERENCE_PATTERN = re.compile(r"^process-([1-9][0-9]*)$")

_PositiveTextBytes = Annotated[int, Field(gt=0, le=_MAX_MODEL_TEXT_BYTES)]
_PositiveOutputBytes = Annotated[int, Field(gt=0, le=_MAX_MODEL_OUTPUT_BYTES)]
_PositiveResults = Annotated[int, Field(gt=0, le=_MAX_MODEL_RESULTS)]
_NonNegativeOffset = Annotated[int, Field(ge=0)]
_PositiveTimeout = Annotated[float, Field(gt=0, allow_inf_nan=False)]
_NonNegativeTimeout = Annotated[float, Field(ge=0, allow_inf_nan=False)]


@dataclass(slots=True)
class _ReferenceEntry:
    reference: str
    value: object
    expires_at: datetime | None
    active: bool = True
    stdout_offset: int = 0
    stderr_offset: int = 0
    drain_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


@dataclass(slots=True)
class _ReferenceReservation:
    table: _CompactReferenceTable
    remaining: int
    created: list[str] = field(default_factory=list)
    active: bool = True


class _CompactReferenceTable:
    """One bounded monotonic process-reference table for a logical run."""

    def __init__(self, max_entries: int) -> None:
        self._max_entries = max_entries
        self._next = 1
        self._entries: dict[str, _ReferenceEntry] = {}
        self._keys: dict[object, str] = {}
        self._reserved = 0
        self._lock = threading.RLock()

    @contextmanager
    def reserve(self, slots: int) -> Iterator[_ReferenceReservation]:
        if slots < 0:
            raise ValueError("reference reservation slots cannot be negative")
        with self._lock:
            if len(self._entries) + self._reserved + slots > self._max_entries:
                raise EnvironmentError(
                    "Environment compact reference capacity is exhausted.",
                    code="environment_reference_exhausted",
                )
            self._reserved += slots
        reservation = _ReferenceReservation(table=self, remaining=slots)
        try:
            yield reservation
        except BaseException:
            if reservation.active:
                self._close_reservation(reservation, rollback=True)
            raise
        else:
            if reservation.active:
                self._close_reservation(reservation, rollback=False)

    @contextmanager
    def projection(self, reservation: _ReferenceReservation) -> Iterator[None]:
        with self._lock:
            try:
                yield
            except BaseException:
                self._close_reservation(reservation, rollback=True)
                raise
            else:
                self._close_reservation(reservation, rollback=False)

    def register(
        self,
        kind: Literal["process"],
        value: object,
        *,
        expires_at: datetime | None = None,
        reservation: _ReferenceReservation | None = None,
    ) -> str:
        del kind
        with self._lock:
            existing_ref = self._keys.get(value)
            if existing_ref is not None:
                existing = self._entries[existing_ref]
                self._expire(existing)
                if not existing.active:
                    raise EnvironmentError(
                        "Environment compact reference is no longer available.",
                        code="environment_reference_stale",
                    )
                if expires_at is not None and (existing.expires_at is None or expires_at < existing.expires_at):
                    existing.expires_at = expires_at
                return existing.reference
            if reservation is not None:
                if reservation.table is not self or not reservation.active or reservation.remaining <= 0:
                    raise EnvironmentError(
                        "Environment compact reference reservation is exhausted.",
                        code="environment_reference_exhausted",
                    )
                reservation.remaining -= 1
                self._reserved -= 1
            elif len(self._entries) + self._reserved >= self._max_entries:
                raise EnvironmentError(
                    "Environment compact reference capacity is exhausted.",
                    code="environment_reference_exhausted",
                )
            sequence = self._next
            self._next += 1
            reference = f"process-{sequence}"
            self._entries[reference] = _ReferenceEntry(
                reference=reference,
                value=value,
                expires_at=expires_at,
            )
            self._keys[value] = reference
            if reservation is not None:
                reservation.created.append(reference)
            return reference

    def _close_reservation(self, reservation: _ReferenceReservation, *, rollback: bool) -> None:
        with self._lock:
            if reservation.table is not self or not reservation.active:
                raise RuntimeError("compact reference reservation is not active")
            if rollback:
                for reference in reversed(reservation.created):
                    entry = self._entries.pop(reference)
                    if self._keys.get(entry.value) == reference:
                        self._keys.pop(entry.value)
            self._reserved -= reservation.remaining
            reservation.remaining = 0
            reservation.active = False

    def entry(self, reference: str) -> _ReferenceEntry:
        if _REFERENCE_PATTERN.fullmatch(reference) is None:
            raise EnvironmentError(
                "Expected a process compact reference.",
                code="environment_reference_invalid",
            )
        with self._lock:
            entry = self._entries.get(reference)
            if entry is None:
                raise EnvironmentError(
                    "Environment compact reference is unknown.",
                    code="environment_reference_invalid",
                )
            self._expire(entry)
            if not entry.active:
                raise EnvironmentError(
                    "Environment compact reference is no longer available.",
                    code="environment_reference_stale",
                )
            return entry

    def resolve(self, reference: str, kind: Literal["process"]) -> object:
        del kind
        return self.entry(reference).value

    def tombstone(self, reference: str, kind: Literal["process"]) -> None:
        del kind
        with self._lock:
            entry = self._entries.get(reference)
            if entry is None:
                raise EnvironmentError(
                    "Environment compact reference is unknown.",
                    code="environment_reference_invalid",
                )
            entry.active = False

    def active_page(
        self,
        kind: Literal["process"],
        *,
        cursor: int,
        limit: int,
    ) -> tuple[tuple[tuple[str, object], ...], int | None]:
        del kind
        with self._lock:
            page: list[tuple[str, object]] = []
            last_sequence = cursor
            has_more = False
            for reference, entry in self._entries.items():
                match = _REFERENCE_PATTERN.fullmatch(reference)
                if match is None:
                    raise AssertionError("compact reference table contains an invalid reference")
                sequence = int(match.group(1))
                if sequence <= cursor:
                    continue
                self._expire(entry)
                if not entry.active:
                    continue
                if len(page) < limit:
                    page.append((reference, entry.value))
                    last_sequence = sequence
                    continue
                has_more = True
                break
            return tuple(page), last_sequence if has_more else None

    @staticmethod
    def _expire(entry: _ReferenceEntry) -> None:
        if entry.expires_at is not None and datetime.now(UTC) >= entry.expires_at:
            entry.active = False


class ShellProcessProjector(Protocol):
    def process_start_resource_resolver(self) -> ToolResourceResolver: ...

    async def start_process(
        self,
        ctx: RunContext[AgentContext],
        request: CommandRequest,
        *,
        alias: str | None,
    ) -> tuple[ProcessInfo, ProcessProjection]: ...


class ShellToolset:
    """Standard command-tool semantics reusable with provider-neutral bound ports."""

    def __init__(
        self,
        *,
        shell: BoundShellOperations | None = None,
        processes: BoundProcessOperations | None = None,
        outputs: BoundOutputOperations | None = None,
        ports: BoundPortOperations | None = None,
        max_reference_entries: int = 1_024,
        resource_resolver: Callable[[str], ToolResourceResolver] | None = None,
        execution_guard: Callable[[], None] | None = None,
        shell_tools: bool | None = None,
        process_tools: bool | None = None,
        port_tools: bool | None = None,
    ) -> None:
        if not 0 < max_reference_entries <= _MAX_REFERENCE_ENTRIES:
            raise ValueError(f"max_reference_entries must be between 1 and {_MAX_REFERENCE_ENTRIES}")
        self._shell = shell
        self._processes = processes
        self._outputs = outputs
        self._ports = ports
        self._references = _CompactReferenceTable(max_reference_entries)
        self._reference_reservation: ContextVar[_ReferenceReservation | None] = ContextVar(
            f"shell_reference_reservation_{id(self)}",
            default=None,
        )
        self._resource_resolver = resource_resolver
        self._execution_guard = execution_guard
        self._exec_tools = shell is not None if shell_tools is None else shell_tools
        self._process_tools = processes is not None if process_tools is None else process_tools
        self._port_tools = ports is not None if port_tools is None else port_tools
        if self._exec_tools and shell is None:
            raise ValueError("shell_tools requires a shell operations port")
        if self._process_tools and processes is None:
            raise ValueError("process_tools requires a process operations port")
        if self._port_tools and ports is None:
            raise ValueError("port_tools requires a port operations port")
        if (self._exec_tools or self._process_tools) and outputs is None:
            raise ValueError("shell or process tools require an output operations port")

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        tools: list[HarnessTool] = []
        arbitrary_command_effects: set[ToolEffect] = {
            "read",
            "write",
            "delete",
            "execute",
            "external_communication",
        }
        if self._exec_tools:
            tools.append(
                self._tool(
                    self.environment_shell_exec,
                    "environment.shell_exec",
                    arbitrary_command_effects,
                    "none",
                )
            )
        if self._process_tools:
            tools.extend(
                (
                    self._tool(
                        self.environment_process_start,
                        "environment.process_start",
                        arbitrary_command_effects,
                        "none",
                    ),
                    self._tool(
                        self.environment_process_inspect,
                        "environment.process_inspect",
                        {"read"},
                        "read_only",
                    ),
                    self._tool(
                        self.environment_process_status,
                        "environment.process_status",
                        {"read"},
                        "read_only",
                    ),
                    self._tool(
                        self.environment_process_read_output,
                        "environment.process_read_output",
                        {"read"},
                        "none",
                    ),
                    self._tool(
                        self.environment_process_write_stdin,
                        "environment.process_write_stdin",
                        {"write"},
                        "none",
                    ),
                    self._tool(
                        self.environment_process_close_stdin,
                        "environment.process_close_stdin",
                        {"write"},
                        "none",
                    ),
                    self._tool(
                        self.environment_process_signal,
                        "environment.process_signal",
                        {"execute"},
                        "none",
                    ),
                    self._tool(
                        self.environment_process_wait,
                        "environment.process_wait",
                        {"read"},
                        "none",
                    ),
                    self._tool(
                        self.environment_process_kill,
                        "environment.process_kill",
                        {"execute"},
                        "none",
                    ),
                    self._tool(
                        self.environment_process_release,
                        "environment.process_release",
                        {"delete"},
                        "none",
                    ),
                )
            )
        if self._port_tools:
            tools.extend(
                (
                    self._tool(
                        self.environment_port_inspect,
                        "environment.port_inspect",
                        {"read"},
                        "read_only",
                    ),
                    self._tool(
                        self.environment_port_wait,
                        "environment.port_wait",
                        {"read"},
                        "read_only",
                    ),
                )
            )
        return FunctionToolset(tools=tools, id="a13n-shell-tools")

    def _tool(
        self,
        function: Callable[..., object],
        tool_id: str,
        effects: set[ToolEffect],
        idempotency: Literal["none", "read_only"],
    ) -> HarnessTool:
        return HarnessTool(
            function,
            harness_metadata=HarnessToolMetadata(
                tool_id=tool_id,
                effects=frozenset(effects),
                credential_audiences=(),
                idempotency=idempotency,
                output_policy=ToolOutputPolicy(
                    max_inline_bytes=256 * 1024,
                    max_output_bytes=4 * 1024 * 1024,
                    overflow="truncate",
                    redact=True,
                ),
                resource_resolver=(self._resource_resolver(tool_id) if self._resource_resolver is not None else None),
            ),
        )

    async def environment_shell_exec(
        self,
        ctx: RunContext[AgentContext],
        command: ArgvCommand | ShellCommand,
        *,
        alias: str | None = None,
        cwd: str | None = None,
        environment: Mapping[str, str] | None = None,
        unset_environment: Sequence[str] = (),
        network: Literal["configured", "deny"] = "configured",
        wall_time_seconds: _PositiveTimeout | None = None,
        initial_stdin: str | None = None,
        max_inline_bytes: _PositiveTextBytes = 64 * 1024,
        max_output_bytes: _PositiveOutputBytes = 1024 * 1024,
    ) -> ShellExecToolResult:
        async def execute() -> Any:
            request = self._command_request(
                command,
                cwd=cwd,
                environment=environment,
                unset_environment=unset_environment,
                network=network,
                wall_time_seconds=wall_time_seconds,
                initial_stdin=initial_stdin,
                keep_stdin_open=False,
                max_inline_bytes=max_inline_bytes,
                max_output_bytes=max_output_bytes,
            )
            return await self._require_shell().exec_captured(request, alias=alias)

        def project(result: Any) -> Mapping[str, object]:
            return {
                "status": self._project_status(result.status),
                "stdout": self._project_capture(result.output.stdout, _capture_initial_bytes(result.output.stdout)),
                "stderr": self._project_capture(result.output.stderr, _capture_initial_bytes(result.output.stderr)),
            }

        projected = await self._execute(execute, project)
        if not isinstance(projected, dict) or projected.get("ok") is not True:
            return cast(ShellExecToolResult, projected)
        content_complete = all(
            isinstance(stream, dict) and stream.get("content_complete") is True
            for stream in (projected.get("stdout"), projected.get("stderr"))
        )
        return cast(
            ShellExecToolResult,
            await disclose_text_paths(
                ctx.deps,
                cast(Mapping[str, JsonValue], projected),
                text_paths=(("stdout", "text"), ("stderr", "text")),
                content_complete=content_complete,
                noun="foreground command result",
                limit=DEFAULT_TOOL_OUTPUT_CHARS,
                preserve_tail=True,
            ),
        )

    async def environment_process_start(
        self,
        ctx: RunContext[AgentContext],
        command: ArgvCommand | ShellCommand,
        *,
        alias: str | None = None,
        cwd: str | None = None,
        environment: Mapping[str, str] | None = None,
        unset_environment: Sequence[str] = (),
        network: Literal["configured", "deny"] = "configured",
        wall_time_seconds: _PositiveTimeout | None = None,
        initial_stdin: str | None = None,
        keep_stdin_open: bool = False,
        max_inline_bytes: _PositiveTextBytes = 64 * 1024,
        max_output_bytes: _PositiveOutputBytes = 1024 * 1024,
    ) -> ProcessToolResult:
        async def execute() -> Any:
            request = self._command_request(
                command,
                cwd=cwd,
                environment=environment,
                unset_environment=unset_environment,
                network=network,
                wall_time_seconds=wall_time_seconds,
                initial_stdin=initial_stdin,
                keep_stdin_open=keep_stdin_open,
                max_inline_bytes=max_inline_bytes,
                max_output_bytes=max_output_bytes,
            )
            return await self.start_process(ctx, request, alias=alias)

        return await self._execute(
            execute,
            lambda result: result[1],
        )

    async def environment_process_inspect(
        self,
        ctx: RunContext[AgentContext],
        process: str,
    ) -> ProcessToolResult:
        return await self._execute(
            lambda: self._require_processes().inspect(self._process(process)),
            self._project_process,
        )

    async def environment_process_status(
        self,
        ctx: RunContext[AgentContext],
        *,
        cursor: _NonNegativeOffset = 0,
        limit: _PositiveResults = 100,
    ) -> ProcessStatusListResult:
        """Read one bounded summary page from this run's process reference domain."""
        selected, next_cursor = self._references.active_page(
            "process",
            cursor=cursor,
            limit=limit,
        )
        processes: list[ProcessStatusItem] = []
        for reference, value in selected:
            from a13n_harness.environment.commands import BoundProcessHandle

            if not isinstance(value, BoundProcessHandle):
                continue
            try:
                info = await self._require_processes().inspect(value)
            except EnvironmentError as exc:
                processes.append(
                    {
                        "process": reference,
                        "ok": False,
                        "error": {"code": exc.code, "retry_hint": exc.retry_hint},
                    }
                )
                continue
            processes.append(
                {
                    "process": reference,
                    "ok": True,
                    "status": self._project_status(info.status),
                    "stdin_open": info.stdin_open,
                    "produced_bytes": {
                        "stdout": info.output.stdout.produced_bytes,
                        "stderr": info.output.stderr.produced_bytes,
                    },
                }
            )
        projected: dict[str, JsonValue] = {
            "ok": True,
            "processes": cast(JsonValue, processes),
            "showing": len(processes),
            "next_cursor": next_cursor,
            "truncated": next_cursor is not None,
        }
        bounded, showing = await disclose_sequence_field(
            ctx.deps,
            projected,
            field="processes",
            content_complete=next_cursor is None,
            noun="process status page",
            continuation_hint="Call environment_process_status again with next_cursor as cursor to continue.",
        )
        bounded["showing"] = showing
        disclosure = bounded.get("disclosure")
        if showing < len(processes) and isinstance(disclosure, dict) and disclosure.get("output_file_path") is None:
            bounded["next_cursor"] = cursor
            bounded["truncated"] = True
            disclosure["hint"] = (
                "Call environment_process_status again with this next_cursor as cursor and a smaller limit."
            )
        return cast(ProcessStatusListResult, bounded)

    async def environment_process_read_output(
        self,
        ctx: RunContext[AgentContext],
        process: str,
        *,
        wait_seconds: _NonNegativeTimeout = 0,
        max_inline_bytes: _PositiveTextBytes = 64 * 1024,
        max_output_bytes: _PositiveOutputBytes = 1024 * 1024,
    ) -> ProcessReadOutputResult:
        del ctx
        return await self._drain_process_output(
            process,
            wait_seconds=wait_seconds,
            max_inline_bytes=max_inline_bytes,
            max_output_bytes=max_output_bytes,
        )

    async def environment_process_write_stdin(
        self,
        ctx: RunContext[AgentContext],
        process: str,
        data: str,
        *,
        close_after_write: bool = False,
    ) -> ProcessWriteStdinResult:
        return await self._execute(
            lambda: self._require_processes().write_stdin(
                self._process(process),
                data.encode("utf-8"),
                close_after_write=close_after_write,
            ),
            lambda result: {"accepted_bytes": result.accepted_bytes, "stdin_open": result.stdin_open},
        )

    async def environment_process_close_stdin(
        self,
        ctx: RunContext[AgentContext],
        process: str,
    ) -> ProcessCloseStdinResult:
        return await self._execute(
            lambda: self._require_processes().close_stdin(self._process(process)),
            lambda result: {"closed": True},
        )

    async def environment_process_signal(
        self,
        ctx: RunContext[AgentContext],
        process: str,
        signal: Literal["interrupt", "terminate"],
    ) -> ProcessSignalResult:
        return await self._execute(
            lambda: self._require_processes().signal(self._process(process), signal),
            lambda result: {
                "accepted": result.accepted,
                "process": self._project_process(result.process),
            },
        )

    async def environment_process_wait(
        self,
        ctx: RunContext[AgentContext],
        process: str,
        *,
        condition: Literal["initial_terminal", "tree_cleaned"] = "tree_cleaned",
        timeout_seconds: _PositiveTimeout,
        max_inline_bytes: _PositiveTextBytes = 64 * 1024,
        max_output_bytes: _PositiveOutputBytes = 1024 * 1024,
    ) -> ProcessReadOutputResult:
        del ctx
        try:
            self._guard_execution()
            await self._require_processes().wait(
                self._process(process),
                condition=condition,
                timeout_seconds=timeout_seconds,
            )
        except EnvironmentError as exc:
            return _environment_error_result(exc)
        return await self._drain_process_output(
            process,
            wait_seconds=0,
            max_inline_bytes=max_inline_bytes,
            max_output_bytes=max_output_bytes,
        )

    async def environment_process_kill(
        self,
        ctx: RunContext[AgentContext],
        process: str,
    ) -> ProcessToolResult:
        return await self._execute(
            lambda: self._require_processes().kill(self._process(process)),
            lambda result: self._project_process(result.process),
        )

    async def environment_process_release(
        self,
        ctx: RunContext[AgentContext],
        process: str,
    ) -> ReleaseResult:
        async def release() -> None:
            handle = self._process(process)
            info = await self._require_processes().inspect(handle)
            for capture in (info.output.stdout, info.output.stderr):
                if capture.reference is None:
                    continue
                try:
                    await self._require_outputs().release(reference=capture.reference)
                except EnvironmentError as exc:
                    if exc.code != "environment_not_found":
                        raise

            async def detach() -> None:
                await self._require_processes().release(handle)
                self._references.tombstone(process, "process")

            cleanup = asyncio.create_task(detach())
            cleanup_error, cancellation = await _await_owned_cleanup(cleanup)
            if cancellation is not None:
                if cleanup_error is not None:
                    cancellation.add_note(f"Process detach also failed: {cleanup_error!r}")
                raise cancellation
            if cleanup_error is not None:
                raise cleanup_error

        return await self._execute(release, lambda result: {"released": True})

    async def environment_port_inspect(
        self,
        ctx: RunContext[AgentContext],
        port: Annotated[int, Field(ge=1, le=65535)],
        *,
        alias: str | None = None,
        address: Literal["loopback", "any"] = "loopback",
    ) -> PortToolResult:
        return await self._execute(
            lambda: self._require_ports().inspect(PortTarget(alias=alias, address=address, port=port)),
            self._project_port,
        )

    async def environment_port_wait(
        self,
        ctx: RunContext[AgentContext],
        port: Annotated[int, Field(ge=1, le=65535)],
        *,
        alias: str | None = None,
        address: Literal["loopback", "any"] = "loopback",
        desired: Literal["listening", "not_listening"] = "listening",
        timeout_seconds: _PositiveTimeout,
    ) -> PortToolResult:
        return await self._execute(
            lambda: self._require_ports().wait(
                PortTarget(alias=alias, address=address, port=port),
                desired=desired,
                timeout_seconds=timeout_seconds,
            ),
            self._project_port,
        )

    async def _execute(
        self,
        operation: Callable[[], Awaitable[Any]],
        project: Callable[[Any], Mapping[str, object] | Awaitable[Mapping[str, object]]],
        *,
        reference_slots: int = 0,
    ) -> Any:
        try:
            self._guard_execution()
            with self._reference_scope(reference_slots):
                result = await operation()
                with self._reference_projection():
                    projected = project(result)
                    if inspect.isawaitable(projected):
                        projected = await projected
                    return {"ok": True, **dict(projected)}
        except EnvironmentError as exc:
            return _environment_error_result(exc)

    @contextmanager
    def _reference_scope(self, slots: int) -> Iterator[None]:
        if slots == 0 or self._reference_reservation.get() is not None:
            yield
            return
        with self._references.reserve(slots) as reservation:
            token = self._reference_reservation.set(reservation)
            try:
                yield
            finally:
                self._reference_reservation.reset(token)

    @contextmanager
    def _reference_projection(self) -> Iterator[None]:
        reservation = self._reference_reservation.get()
        if reservation is None:
            yield
            return
        with self._references.projection(reservation):
            yield

    def _process(self, reference: str) -> Any:
        from a13n_harness.environment.commands import BoundProcessHandle

        value = self._references.resolve(reference, "process")
        if not isinstance(value, BoundProcessHandle):
            raise EnvironmentError("Process reference has an incompatible value.", code="environment_reference_invalid")
        return value

    def resolve_process(self, reference: str) -> Any:
        """Resolve a compact process reference for another scoped Capability."""
        return self._process(reference)

    def process_start_resource_resolver(self) -> ToolResourceResolver:
        """Share the exact managed authorization fence used by process_start."""
        if self._resource_resolver is None:
            raise DefinitionError(
                "Process start resource resolution is unavailable.",
                code="tool_resource_resolver_missing",
            )
        return self._resource_resolver("environment.process_start")

    async def start_process(
        self,
        ctx: RunContext[AgentContext],
        request: CommandRequest,
        *,
        alias: str | None,
    ) -> tuple[ProcessInfo, ProcessProjection]:
        del ctx
        self._guard_execution()
        with self._reference_scope(1):
            result = await self._require_processes().start(request, alias=alias)
            try:
                with self._reference_projection():
                    return result.process, self._project_process(
                        result.process,
                        consume_output=True,
                        output_budget=request.output_policy.max_inline_bytes,
                    )
            except BaseException as projection_error:
                cleanup = asyncio.create_task(self._cleanup_unprojected_process(result.process))
                cleanup_error, cancellation = await _await_owned_cleanup(cleanup)
                if cancellation is not None:
                    cancellation.add_note(f"Process projection had already failed: {projection_error!r}")
                    if cleanup_error is not None:
                        cancellation.add_note(f"Unprojected process cleanup also failed: {cleanup_error!r}")
                    raise cancellation from projection_error
                if cleanup_error is not None:
                    projection_error.add_note(f"Unprojected process cleanup also failed: {cleanup_error!r}")
                raise

    async def _cleanup_unprojected_process(self, process: ProcessInfo) -> None:
        processes = self._require_processes()
        failures: list[BaseException] = []
        try:
            await processes.kill(process.handle)
        except BaseException as exc:
            failures.append(exc)
        try:
            await processes.wait(
                process.handle,
                condition="tree_cleaned",
                timeout_seconds=5.0,
            )
        except BaseException as exc:
            failures.append(exc)
        try:
            await processes.release(process.handle)
        except BaseException as exc:
            failures.append(exc)
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise BaseExceptionGroup("Unprojected process cleanup failed", failures)

    def _project_process(
        self,
        process: ProcessInfo,
        *,
        consume_output: bool = False,
        output_budget: int = _MAX_MODEL_TEXT_BYTES,
    ) -> ProcessProjection:
        reservation = self._reference_reservation.get()
        reference = self._references.register("process", process.handle, reservation=reservation)
        entry = self._references.entry(reference)
        stdout_available = _capture_initial_bytes(process.output.stdout) if consume_output else b""
        stderr_available = _capture_initial_bytes(process.output.stderr) if consume_output else b""

        def project(stdout_data: bytes, stderr_data: bytes) -> ProcessProjection:
            projected: ProcessProjection = {
                "process": reference,
                "status": self._project_status(process.status),
                "stdin_open": process.stdin_open,
                "stdout": self._project_capture(process.output.stdout, stdout_data),
                "stderr": self._project_capture(process.output.stderr, stderr_data),
            }
            if consume_output and (
                entry.stdout_offset + len(stdout_data) < process.output.stdout.available_end
                or entry.stderr_offset + len(stderr_data) < process.output.stderr.available_end
            ):
                projected["disclosure"] = continuation_disclosure(
                    cast(Mapping[str, JsonValue], projected),
                    hint="Call environment_process_read_output to read the next retained output page.",
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
        entry.stdout_offset = max(entry.stdout_offset, len(stdout_data))
        entry.stderr_offset = max(entry.stderr_offset, len(stderr_data))
        return projected

    @staticmethod
    def _project_status(status: ProcessStatus) -> ProcessStatusProjection:
        return {
            "phase": status.phase,
            "termination_reason": status.termination_reason,
            "exit_code": status.exit_code,
            "signal": status.signal,
            "cleanup": status.cleanup,
        }

    @staticmethod
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

    async def _drain_process_output(
        self,
        process_reference: str,
        *,
        wait_seconds: float,
        max_inline_bytes: int,
        max_output_bytes: int,
    ) -> ProcessReadOutputResult:
        try:
            self._guard_execution()
            entry = self._references.entry(process_reference)
            from a13n_harness.environment.commands import BoundProcessHandle

            if not isinstance(entry.value, BoundProcessHandle):
                raise EnvironmentError(
                    "Process reference has an incompatible value.",
                    code="environment_reference_invalid",
                )
            async with entry.drain_lock:
                aggregate_budget = min(max_inline_bytes, max_output_bytes)
                policy = EnvironmentOutputPolicy(
                    max_inline_bytes=max(1, aggregate_budget),
                    max_output_bytes=max_output_bytes,
                    overflow="truncate",
                )
                result = await self._require_processes().read_output(
                    entry.value,
                    stdout_start_offset=entry.stdout_offset,
                    stderr_start_offset=entry.stderr_offset,
                    wait_seconds=wait_seconds,
                    policy=policy,
                )
                stdout_available, _ = _materialize_segments(result.stdout.chunks, entry.stdout_offset)
                stderr_available, _ = _materialize_segments(result.stderr.chunks, entry.stderr_offset)
                process_projection = self._project_process(result.process)

                def project(stdout_data: bytes, stderr_data: bytes) -> dict[str, JsonValue]:
                    projected: dict[str, JsonValue] = {
                        "ok": True,
                        "process": cast(JsonValue, process_projection),
                        "stdout": cast(JsonValue, self._project_capture(result.stdout.capture, stdout_data)),
                        "stderr": cast(JsonValue, self._project_capture(result.stderr.capture, stderr_data)),
                    }
                    if (
                        entry.stdout_offset + len(stdout_data) < result.stdout.capture.available_end
                        or entry.stderr_offset + len(stderr_data) < result.stderr.capture.available_end
                    ):
                        projected["disclosure"] = cast(
                            JsonValue,
                            continuation_disclosure(
                                projected,
                                hint=(
                                    "Call environment_process_read_output again to read the next retained output page."
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
                entry.stdout_offset += len(stdout_data)
                entry.stderr_offset += len(stderr_data)
                return cast(ProcessReadOutputResult, acknowledge_tool_output(projected))
        except EnvironmentError as exc:
            return _environment_error_result(exc)

    @staticmethod
    def _project_port(observation: PortObservation) -> PortProjection:
        return {
            "port": observation.target.port,
            "address": observation.target.address,
            "status": observation.status,
            "observed_at": observation.observed_at.isoformat(),
        }

    @staticmethod
    def _output_policy(max_inline_bytes: int, max_output_bytes: int) -> EnvironmentOutputPolicy:
        if max_inline_bytes > max_output_bytes:
            raise EnvironmentError(
                "max_inline_bytes cannot exceed max_output_bytes.",
                code="environment_request_invalid",
            )
        return EnvironmentOutputPolicy(
            max_inline_bytes=max_inline_bytes,
            max_output_bytes=max_output_bytes,
            overflow="retain",
        )

    def _command_request(
        self,
        command: ArgvCommand | ShellCommand,
        *,
        cwd: str | None,
        environment: Mapping[str, str] | None,
        unset_environment: Sequence[str],
        network: Literal["configured", "deny"],
        wall_time_seconds: float | None,
        initial_stdin: str | None,
        keep_stdin_open: bool,
        max_inline_bytes: int,
        max_output_bytes: int,
    ) -> CommandRequest:
        try:
            return CommandRequest(
                command=command,
                cwd=cwd,
                environment=CommandEnvironment(set=dict(environment or {}), unset=tuple(unset_environment)),
                network=network,
                limits=CommandLimits(wall_time_seconds=wall_time_seconds),
                initial_stdin=initial_stdin.encode("utf-8") if initial_stdin is not None else None,
                keep_stdin_open=keep_stdin_open,
                output_policy=self._output_policy(max_inline_bytes, max_output_bytes),
            )
        except EnvironmentError:
            raise
        except (TypeError, ValueError) as exc:
            raise EnvironmentError(
                "Environment command request is invalid.",
                code="environment_request_invalid",
            ) from exc

    def _guard_execution(self) -> None:
        if self._execution_guard is not None:
            self._execution_guard()

    def _require_shell(self) -> BoundShellOperations:
        if self._shell is None:
            raise EnvironmentError("Shell operation facet is unavailable.", code="environment_unsupported")
        return self._shell

    def _require_processes(self) -> BoundProcessOperations:
        if self._processes is None:
            raise EnvironmentError("Process operation facet is unavailable.", code="environment_unsupported")
        return self._processes

    def _require_outputs(self) -> BoundOutputOperations:
        if self._outputs is None:
            raise EnvironmentError("Output operation facet is unavailable.", code="environment_unsupported")
        return self._outputs

    def _require_ports(self) -> BoundPortOperations:
        if self._ports is None:
            raise EnvironmentError("Port operation facet is unavailable.", code="environment_unsupported")
        return self._ports


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
    """Keep an arbitrary byte prefix from splitting a known valid UTF-8 sequence."""
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


def _fit_stream_projection[ProjectionT](
    stdout: bytes,
    stderr: bytes,
    *,
    raw_budget: int,
    json_budget: int,
    project: Callable[[bytes, bytes], ProjectionT],
) -> tuple[bytes, bytes, ProjectionT]:
    """Fit contiguous raw prefixes against the actual serialized semantic result."""
    selected_stdout, selected_stderr = _fit_stream_prefixes(stdout, stderr, 0)
    selected_projection = project(selected_stdout, selected_stderr)
    high = min(max(raw_budget, 0), len(stdout) + len(stderr))
    low = 1
    while low <= high:
        candidate_budget = (low + high) // 2
        candidate_stdout, candidate_stderr = _fit_stream_prefixes(
            stdout,
            stderr,
            candidate_budget,
        )
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
            raise EnvironmentError(
                "Process output contains a gap.",
                code="environment_output_gap",
            )
        chunks.append(segment.data)
        expected += len(segment.data)
    return b"".join(chunks), expected


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


def _environment_error_result(exc: EnvironmentError) -> ToolFailure:
    safe_details: dict[str, JsonValue] = {}
    timeout = exc.details.get("timeout_seconds")
    if isinstance(timeout, int | float) and not isinstance(timeout, bool):
        safe_details["timeout_seconds"] = timeout
    missing = exc.details.get("missing")
    if isinstance(missing, list) and all(isinstance(item, str) for item in missing):
        safe_details["missing"] = cast(JsonValue, list(missing))
    return {
        "ok": False,
        "error": {
            "code": exc.code,
            "retry_hint": exc.retry_hint,
            "details": safe_details,
        },
    }


__all__ = ["ShellProcessProjector", "ShellToolset"]
