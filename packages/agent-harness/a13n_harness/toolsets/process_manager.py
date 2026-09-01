"""Private Run-owned process controller over the entered Environment."""

from __future__ import annotations

import asyncio
import codecs
import re
import secrets
from collections import OrderedDict
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal, cast

from pydantic import JsonValue
from pydantic_ai import RunContext

from a13n_harness.context import AgentContext
from a13n_harness.environment.commands import (
    BoundProcessHandle,
    CommandRequest,
    ProcessInfo,
    ProcessOutputSnapshot,
    ProcessReadOutputResult,
    ProcessStatus,
    ProcessStreamRead,
)
from a13n_harness.environment.models import EnvironmentAction, EnvironmentError
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.environment.retention import EnvironmentOutputCapture, EnvironmentOutputPolicy

from .output import tool_output_size
from .shell_results import (
    OutputPageProjection,
    ProcessInputSuccess,
    ProcessObservationSuccess,
    ProcessSignalSuccess,
    ProcessStatusProjection,
    ShellExecSuccess,
)

_MAX_MODEL_OUTPUT_BYTES = 1024 * 1024
_DEFAULT_INLINE_BYTES = 64 * 1024
_MAX_REFERENCE_ENTRIES = 100_000
_MAX_PROJECTED_OUTPUT_CHARS = 10_500
_REFERENCE_PATTERN = re.compile(r"^process-([0-9a-f]{4})-([1-9][0-9]*)$")
_TERMINAL_PHASES = frozenset({"exited", "signaled", "timed_out", "cancelled", "failed"})
_RUN_PROCESS_ACTIONS = frozenset(
    {
        EnvironmentAction.SHELL_EXEC,
        EnvironmentAction.PROCESS_START,
        EnvironmentAction.PROCESS_INSPECT,
        EnvironmentAction.PROCESS_READ_OUTPUT,
        EnvironmentAction.PROCESS_WRITE_STDIN,
        EnvironmentAction.PROCESS_CLOSE_STDIN,
        EnvironmentAction.PROCESS_SIGNAL,
        EnvironmentAction.PROCESS_WAIT,
        EnvironmentAction.PROCESS_KILL,
        EnvironmentAction.PROCESS_RELEASE,
        EnvironmentAction.OUTPUT_RELEASE,
    }
)


@dataclass(slots=True)
class _ProcessEntry:
    process_id: str
    handle: BoundProcessHandle
    latest: ProcessInfo
    control_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    watcher: asyncio.Task[None] | None = None
    model_visible: asyncio.Event = field(default_factory=asyncio.Event)


class _RunProcessController:
    """Own process references and cleanup for one logical Harness Run."""

    def __init__(
        self,
        environment: BoundEnvironment,
        *,
        execution_guard: Callable[[], None] | None = None,
    ) -> None:
        if not isinstance(environment, BoundEnvironment):
            raise TypeError("environment must be a BoundEnvironment")
        self._environment = environment
        self._execution_guard = execution_guard
        self._incarnation = secrets.token_hex(2)
        self._entries: OrderedDict[str, _ProcessEntry] = OrderedDict()
        self._next_sequence = 1
        self._admission_lock = asyncio.Lock()
        self._context: AgentContext | None = None
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    @asynccontextmanager
    async def active_run(self, ctx: RunContext[AgentContext]) -> AsyncGenerator[None]:
        """Attach the current native attempt only for best-effort readiness."""
        if self._context is None:
            self._context = ctx.deps
        elif self._context is not ctx.deps:
            raise RuntimeError("Run process controller cannot cross logical Runs")
        yield

    def resource_id(self, process_id: str) -> str:
        """Resolve a model selector to a private provider process identity."""
        return self._entry(process_id).handle.identity.process_id

    async def start(
        self,
        request: CommandRequest,
        *,
        alias: str | None,
        yield_time_seconds: float,
        expected_mount_id: str | None = None,
    ) -> ShellExecSuccess:
        self._guard_execution()
        published = False
        entry: _ProcessEntry | None = None
        async with self._admission_lock:
            self._assert_open()
            process_id = self._reserve_reference()
            started = await self._environment.processes.start(
                request,
                alias=alias,
                required_actions=_RUN_PROCESS_ACTIONS,
                expected_mount_id=expected_mount_id,
            )
            entry = _ProcessEntry(
                process_id=process_id,
                handle=started.process.handle,
                latest=started.process,
            )
            try:
                entry.watcher = asyncio.create_task(
                    self._watch(entry),
                    name=f"{process_id}-completion",
                )
                self._entries[process_id] = entry
                published = True
            except BaseException:
                await _await_cleanup_shielded(self._kill_and_release(entry))
                raise

        assert entry is not None
        try:
            if yield_time_seconds > 0:
                try:
                    baseline = entry.latest
                    waited = await self._environment.processes.wait(
                        entry.handle,
                        condition="tree_cleaned",
                        timeout_seconds=yield_time_seconds,
                    )
                    self._adopt_info(entry, waited, baseline=baseline)
                except EnvironmentError as exc:
                    if exc.code != "environment_timeout":
                        raise
            observation = await self._observe(
                entry,
                stdout_offset=0,
                stderr_offset=0,
                wait_seconds=0,
            )
            if _is_final(observation[0]):
                result = _shell_exec_result(None, observation, bound_output=False)
                await _await_cleanup_shielded(self._retire(entry))
                return result
            entry.model_visible.set()
            return _shell_exec_result(process_id, observation)
        except BaseException:
            if not published:
                await _await_cleanup_shielded(self._kill_and_release(entry))
            raise

    async def wait(
        self,
        process_id: str,
        *,
        stdout_offset: int,
        stderr_offset: int,
        timeout_seconds: float,
    ) -> ProcessObservationSuccess:
        self._guard_execution()
        entry = self._entry(process_id)
        observation = await self._observe(
            entry,
            stdout_offset=stdout_offset,
            stderr_offset=stderr_offset,
            wait_seconds=timeout_seconds,
        )
        return _process_observation(
            entry.process_id,
            observation,
            stdout_offset=stdout_offset,
            stderr_offset=stderr_offset,
        )

    async def write_input(
        self,
        process_id: str,
        data: str,
        *,
        close_stdin: bool,
    ) -> ProcessInputSuccess:
        self._guard_execution()
        entry = self._entry(process_id)
        async with entry.control_lock:
            self._require_live(entry)
            encoded = data.encode("utf-8")
            accepted_bytes = 0
            if encoded:
                result = await self._environment.processes.write_stdin(
                    entry.handle,
                    encoded,
                    close_after_write=False,
                )
                accepted_bytes = result.accepted_bytes
            if close_stdin:
                await self._environment.processes.close_stdin(entry.handle)
            baseline = entry.latest
            inspected = await self._environment.processes.inspect(entry.handle)
            self._adopt_info(entry, inspected, baseline=baseline)
        return {
            "ok": True,
            "process_id": process_id,
            "accepted_bytes": accepted_bytes,
            "stdin_open": entry.latest.stdin_open,
            "status": _project_status(entry.latest.status),
        }

    async def signal(
        self,
        process_id: str,
        signal: Literal["interrupt", "terminate", "kill"],
    ) -> ProcessSignalSuccess:
        self._guard_execution()
        entry = self._entry(process_id)
        async with entry.control_lock:
            self._require_live(entry)
            baseline = entry.latest
            if signal == "kill":
                result = await self._environment.processes.kill(entry.handle)
                self._adopt_info(entry, result.process, baseline=baseline)
                accepted = True
            else:
                result = await self._environment.processes.signal(entry.handle, signal)
                self._adopt_info(entry, result.process, baseline=baseline)
                accepted = result.accepted
        return {
            "ok": True,
            "process_id": process_id,
            "accepted": accepted,
            "stdin_open": entry.latest.stdin_open,
            "status": _project_status(entry.latest.status),
        }

    async def _observe(
        self,
        entry: _ProcessEntry,
        *,
        stdout_offset: int,
        stderr_offset: int,
        wait_seconds: float,
    ) -> tuple[ProcessInfo, ProcessReadOutputResult]:
        if wait_seconds > 0:
            try:
                baseline = entry.latest
                waited = await self._environment.processes.wait(
                    entry.handle,
                    condition="tree_cleaned",
                    timeout_seconds=wait_seconds,
                )
                self._adopt_info(entry, waited, baseline=baseline)
            except EnvironmentError as exc:
                if exc.code != "environment_timeout":
                    raise
        baseline = entry.latest
        inspected = await self._environment.processes.inspect(entry.handle)
        self._adopt_info(entry, inspected, baseline=baseline)
        output = await self._environment.processes.read_output(
            entry.handle,
            stdout_start_offset=stdout_offset,
            stderr_start_offset=stderr_offset,
            wait_seconds=0,
            policy=EnvironmentOutputPolicy(
                max_inline_bytes=(_MAX_MODEL_OUTPUT_BYTES if _is_final(entry.latest) else _DEFAULT_INLINE_BYTES),
                max_output_bytes=_MAX_MODEL_OUTPUT_BYTES,
                overflow="retain",
            ),
        )
        self._validate_output(
            entry,
            output,
            minimum_info=inspected,
            stdout_offset=stdout_offset,
            stderr_offset=stderr_offset,
        )
        observed = output.process.model_copy(
            update={
                "output": ProcessOutputSnapshot(
                    stdout=output.stdout.capture,
                    stderr=output.stderr.capture,
                )
            }
        )
        self._adopt_info(entry, observed, baseline=inspected)
        return observed, output

    async def _watch(self, entry: _ProcessEntry) -> None:
        try:
            while True:
                try:
                    baseline = entry.latest
                    waited = await self._environment.processes.wait(
                        entry.handle,
                        condition="tree_cleaned",
                        timeout_seconds=180,
                    )
                    self._adopt_info(entry, waited, baseline=baseline)
                    if _is_final(entry.latest):
                        break
                except EnvironmentError as exc:
                    if exc.code != "environment_timeout":
                        return
            await entry.model_visible.wait()
            if self._entries.get(entry.process_id) is not entry or self._closed:
                return
            context = self._context
            if context is not None:
                await context._steering.notify(
                    (
                        f"Background process {entry.process_id} has finished. "
                        "Call shell_wait with your last returned stdout_offset and "
                        "stderr_offset to inspect its final output."
                    ),
                    source="background_process",
                    references=(entry.process_id,),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Readiness is advisory. Explicit shell_wait polling remains authoritative.
            return

    async def _retire(self, entry: _ProcessEntry) -> None:
        watcher = entry.watcher
        if watcher is not None and watcher is not asyncio.current_task() and not watcher.done():
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        await self._release_owned_resources(entry)
        if self._entries.get(entry.process_id) is entry:
            self._entries.pop(entry.process_id, None)

    async def close(self) -> None:
        async with self._admission_lock:
            if self._close_task is None:
                self._closed = True
                self._close_task = asyncio.create_task(
                    self._close_owned(),
                    name=f"run-processes-{self._incarnation}-close",
                )
            close_task = self._close_task
        await _await_cleanup_shielded(close_task)

    async def _close_owned(self) -> None:
        entries = tuple(self._entries.values())
        failures: list[BaseException] = []
        for entry in entries:
            try:
                await self._kill_and_release(entry)
            except BaseException as exc:
                failures.append(exc)
            finally:
                if self._entries.get(entry.process_id) is entry:
                    self._entries.pop(entry.process_id, None)
        if failures:
            raise BaseExceptionGroup("Run-owned process cleanup failed", failures)

    async def _kill_and_release(self, entry: _ProcessEntry) -> None:
        watcher = entry.watcher
        if watcher is not None and watcher is not asyncio.current_task() and not watcher.done():
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        failures: list[BaseException] = []
        try:
            if not _is_final(entry.latest):
                baseline = entry.latest
                result = await self._environment.processes.kill(entry.handle)
                self._adopt_info(entry, result.process, baseline=baseline)
        except EnvironmentError as exc:
            if exc.code != "environment_not_found":
                failures.append(exc)
        try:
            await self._release_owned_resources(entry)
        except BaseException as exc:
            failures.append(exc)
        if failures:
            raise BaseExceptionGroup("Run-owned process resource cleanup failed", failures)

    async def _release_owned_resources(self, entry: _ProcessEntry) -> None:
        failures: list[BaseException] = []
        try:
            await self._environment.processes.release(entry.handle)
        except EnvironmentError as exc:
            if exc.code != "environment_not_found":
                failures.append(exc)
        references = []
        for capture in (entry.latest.output.stdout, entry.latest.output.stderr):
            reference = capture.reference
            if reference is not None and reference not in references:
                references.append(reference)
        for reference in references:
            try:
                await self._environment.outputs.release(reference=reference)
            except EnvironmentError as exc:
                if exc.code != "environment_not_found":
                    failures.append(exc)
        if failures:
            raise BaseExceptionGroup("Run-owned process release failed", failures)

    def _adopt_info(
        self,
        entry: _ProcessEntry,
        info: ProcessInfo,
        *,
        baseline: ProcessInfo | None = None,
    ) -> None:
        if info.handle != entry.handle:
            raise EnvironmentError(
                "The process provider returned a mismatched handle.",
                code="environment_provider_failure",
            )
        expected = (baseline or entry.latest).output
        current = info.output
        if (
            current.stdout.produced_bytes < expected.stdout.produced_bytes
            or current.stderr.produced_bytes < expected.stderr.produced_bytes
        ):
            raise EnvironmentError(
                "The process provider returned non-monotonic output metadata.",
                code="environment_provider_failure",
            )
        _validate_capture(current.stdout)
        _validate_capture(current.stderr)
        latest_info = entry.latest
        if latest_info.status.phase in _TERMINAL_PHASES and info.status.phase not in _TERMINAL_PHASES:
            return
        latest = latest_info.output
        if (
            current.stdout.produced_bytes >= latest.stdout.produced_bytes
            and current.stderr.produced_bytes >= latest.stderr.produced_bytes
        ):
            entry.latest = info

    def _validate_output(
        self,
        entry: _ProcessEntry,
        output: ProcessReadOutputResult,
        *,
        minimum_info: ProcessInfo,
        stdout_offset: int,
        stderr_offset: int,
    ) -> None:
        if output.process.handle != entry.handle:
            raise EnvironmentError(
                "The process provider returned output for another handle.",
                code="environment_provider_failure",
            )
        _validate_stream(output.stdout, requested_offset=stdout_offset)
        _validate_stream(output.stderr, requested_offset=stderr_offset)
        if (
            output.stdout.capture.produced_bytes < minimum_info.output.stdout.produced_bytes
            or output.stderr.capture.produced_bytes < minimum_info.output.stderr.produced_bytes
        ):
            raise EnvironmentError(
                "The process provider returned non-monotonic output page metadata.",
                code="environment_provider_failure",
            )
        if sum(len(chunk.data) for chunk in (*output.stdout.chunks, *output.stderr.chunks)) > (
            2 * _MAX_MODEL_OUTPUT_BYTES
        ):
            raise EnvironmentError(
                "The process provider exceeded Harness output bounds.",
                code="environment_provider_failure",
            )

    def _reserve_reference(self) -> str:
        if len(self._entries) >= _MAX_REFERENCE_ENTRIES:
            raise EnvironmentError(
                "The Run process reference limit has been reached.",
                code="environment_limit_exceeded",
            )
        process_id = f"process-{self._incarnation}-{self._next_sequence}"
        self._next_sequence += 1
        return process_id

    def _entry(self, process_id: str) -> _ProcessEntry:
        self._assert_open()
        match = _REFERENCE_PATTERN.fullmatch(process_id)
        if match is None or match.group(1) != self._incarnation:
            raise EnvironmentError(
                "The process reference is unknown in this Run.",
                code="environment_not_found",
            )
        entry = self._entries.get(process_id)
        if entry is None:
            raise EnvironmentError(
                "The process reference is unknown in this Run.",
                code="environment_not_found",
            )
        return entry

    @staticmethod
    def _require_live(entry: _ProcessEntry) -> None:
        if entry.latest.status.phase in _TERMINAL_PHASES:
            raise EnvironmentError(
                "The process is no longer running.",
                code="environment_conflict",
            )

    def _assert_open(self) -> None:
        if self._closed:
            raise EnvironmentError(
                "The Run process controller is closed.",
                code="environment_unavailable",
            )

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


def _validate_capture(capture: EnvironmentOutputCapture) -> None:
    if not (0 <= capture.available_start <= capture.available_end <= capture.produced_bytes):
        raise EnvironmentError(
            "The process provider returned an invalid retained-output range.",
            code="environment_provider_failure",
        )
    if capture.captured_bytes > capture.produced_bytes:
        raise EnvironmentError(
            "The process provider returned invalid captured-output metadata.",
            code="environment_provider_failure",
        )


def _validate_stream(stream: ProcessStreamRead, *, requested_offset: int) -> None:
    capture = stream.capture
    _validate_capture(capture)
    if requested_offset > capture.available_end:
        raise EnvironmentError(
            "The requested output offset is beyond the retained range.",
            code="environment_cursor_invalid",
        )
    expected = max(requested_offset, capture.available_start)
    for chunk in stream.chunks:
        if chunk.start_offset != expected:
            raise EnvironmentError(
                "The process provider returned a non-contiguous output page.",
                code="environment_provider_failure",
            )
        expected += len(chunk.data)
        if expected > capture.available_end:
            raise EnvironmentError(
                "The process provider returned output outside its retained range.",
                code="environment_provider_failure",
            )


def _project_stream(
    stream: ProcessStreamRead,
    *,
    requested_offset: int,
    data: bytes,
    full_page_bytes: int,
) -> OutputPageProjection:
    capture = stream.capture
    start_offset = stream.chunks[0].start_offset if stream.chunks else max(requested_offset, capture.available_start)
    return {
        "requested_offset": requested_offset,
        "start_offset": start_offset,
        "next_offset": start_offset + len(data),
        "available_start": capture.available_start,
        "available_end": capture.available_end,
        "produced_bytes": capture.produced_bytes,
        "producer_complete": capture.producer_complete,
        "content_complete": capture.content_complete and len(data) == full_page_bytes,
        "omitted_before_bytes": max(0, start_offset - requested_offset),
        "text": data.decode("utf-8", errors="replace"),
    }


def _process_observation(
    process_id: str,
    observation: tuple[ProcessInfo, ProcessReadOutputResult],
    *,
    stdout_offset: int,
    stderr_offset: int,
    bound_output: bool = True,
) -> ProcessObservationSuccess:
    info, output = observation
    stdout_full = b"".join(chunk.data for chunk in output.stdout.chunks)
    stderr_full = b"".join(chunk.data for chunk in output.stderr.chunks)
    budget = len(stdout_full) + len(stderr_full)
    while True:
        stdout_data, stderr_data = _fit_stream_prefixes(stdout_full, stderr_full, budget)
        result: ProcessObservationSuccess = {
            "ok": True,
            "process_id": process_id,
            "status": _project_status(info.status),
            "stdin_open": info.stdin_open,
            "stdout": _project_stream(
                output.stdout,
                requested_offset=stdout_offset,
                data=stdout_data,
                full_page_bytes=len(stdout_full),
            ),
            "stderr": _project_stream(
                output.stderr,
                requested_offset=stderr_offset,
                data=stderr_data,
                full_page_bytes=len(stderr_full),
            ),
        }
        if not bound_output or tool_output_size(cast(dict[str, JsonValue], result)) <= _MAX_PROJECTED_OUTPUT_CHARS:
            return result
        if budget == 0:
            raise EnvironmentError(
                "The process result metadata exceeds Harness output bounds.",
                code="environment_provider_failure",
            )
        budget = max(0, int(budget * 0.75) - 1)


def _fit_stream_prefixes(stdout: bytes, stderr: bytes, max_bytes: int) -> tuple[bytes, bytes]:
    """Fit fair UTF-8-safe stream prefixes inside one aggregate byte budget."""
    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    stdout_limit = min(len(stdout), (max_bytes + 1) // 2)
    stderr_limit = min(len(stderr), max_bytes // 2)
    remaining = max_bytes - stdout_limit - stderr_limit
    if remaining:
        added = min(len(stdout) - stdout_limit, remaining)
        stdout_limit += added
        remaining -= added
    if remaining:
        stderr_limit += min(len(stderr) - stderr_limit, remaining)
    return _utf8_safe_prefix(stdout, stdout_limit), _utf8_safe_prefix(stderr, stderr_limit)


def _utf8_safe_prefix(data: bytes, limit: int) -> bytes:
    prefix = data[:limit]
    if limit >= len(data):
        return prefix
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    decoder.decode(prefix, final=False)
    buffered, _ = decoder.getstate()
    return prefix[: len(prefix) - len(buffered)] if buffered else prefix


def _shell_exec_result(
    process_id: str | None,
    observation: tuple[ProcessInfo, ProcessReadOutputResult],
    *,
    bound_output: bool = True,
) -> ShellExecSuccess:
    projected = _process_observation(
        process_id or "",
        observation,
        stdout_offset=0,
        stderr_offset=0,
        bound_output=bound_output,
    )
    result: ShellExecSuccess = {
        "ok": True,
        "status": projected["status"],
        "stdin_open": projected["stdin_open"],
        "stdout": projected["stdout"],
        "stderr": projected["stderr"],
    }
    if process_id is not None:
        result["process_id"] = process_id
    return result


def _is_final(info: ProcessInfo) -> bool:
    return (
        info.status.phase in _TERMINAL_PHASES
        and info.status.cleanup != "pending"
        and info.output.stdout.producer_complete
        and info.output.stderr.producer_complete
    )


async def _await_cleanup_shielded(awaitable: Awaitable[object]) -> None:
    task = asyncio.ensure_future(awaitable)
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if cancellation is None:
                cancellation = exc
    cleanup_error = task.exception()
    if cancellation is not None:
        if cleanup_error is not None:
            cancellation.add_note(f"Cleanup also failed with {type(cleanup_error).__name__}.")
        raise cancellation from cleanup_error
    if cleanup_error is not None:
        raise cleanup_error


__all__ = ["_RUN_PROCESS_ACTIONS", "_RunProcessController", "_fit_stream_prefixes", "_project_status"]
