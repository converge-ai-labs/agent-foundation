"""Direct Local command, process-tree, output, and loopback-port operations."""

from __future__ import annotations

import asyncio
import base64
import errno
import itertools
import math
import os
import signal as os_signal
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex
from typing import TYPE_CHECKING, Literal

from .._local_retention import LocalRetentionStore, LocalRetentionWriter
from ..commands import (
    ArgvCommand,
    BoundProcessHandle,
    CommandRequest,
    PortObservation,
    PortTarget,
    ProcessControlResult,
    ProcessDiscovery,
    ProcessIdentity,
    ProcessInfo,
    ProcessOutputSnapshot,
    ProcessReadOutputResult,
    ProcessSignalResult,
    ProcessStartResult,
    ProcessStatus,
    ProcessStreamRead,
    ProcessWriteStdinResult,
    ShellCommand,
    ShellExecResult,
)
from ..models import EnvironmentError, EnvironmentOperationReceipt
from ..retention import (
    BoundOutputCursor,
    BoundOutputReference,
    EnvironmentOutputCapture,
    EnvironmentOutputPolicy,
    EnvironmentOutputSegment,
    OpaqueProcessHandle,
    _unwrap_opaque,
    materialize_capture,
)

if TYPE_CHECKING:
    from .._windows_job import WindowsJob
    from .configuration import DirectLocalShellProfile
    from .files import LocalFileOperator
    from .shared import (
        _DirectLocalOutputPolicy,
        _DirectLocalPortPolicy,
        _DirectLocalProcessPolicy,
    )


class _OutputCollector:
    def __init__(
        self,
        *,
        policy: EnvironmentOutputPolicy,
        buffer_limit: int,
        writer: LocalRetentionWriter | None,
    ) -> None:
        self.policy = policy
        self.buffer_limit = min(policy.max_inline_bytes, policy.max_output_bytes, buffer_limit)
        self.writer = writer
        self.produced = 0
        self.stored = 0
        self.preview = bytearray()
        self.reference: BoundOutputReference | None = None
        self.complete = False

    async def consume(self, stream: asyncio.StreamReader) -> None:
        try:
            while chunk := await stream.read(65_536):
                self.produced += len(chunk)
                preview_remaining = self.buffer_limit - len(self.preview)
                if preview_remaining > 0:
                    self.preview.extend(chunk[:preview_remaining])
                if self.writer is not None:
                    storage_remaining = self.policy.max_output_bytes - self.stored
                    if storage_remaining > 0:
                        self.stored += await self.writer.write(chunk[:storage_remaining])
        finally:
            self.complete = True

    async def finish(self) -> EnvironmentOutputCapture:
        preview = bytes(self.preview)
        if self.writer is not None:
            if self.stored > len(preview):
                self.reference = await self.writer.commit(
                    producer_complete=self.complete,
                    content_complete=self.produced == self.stored,
                    produced_bytes=self.produced,
                    dropped_bytes=max(self.produced - self.stored, 0),
                )
            else:
                await self.writer.abort()
        if self.reference is not None:
            capture = EnvironmentOutputCapture(
                kind="retained",
                producer_complete=self.complete,
                content_complete=self.produced == self.stored,
                produced_bytes=self.produced,
                captured_bytes=self.stored,
                dropped_bytes=max(self.produced - self.stored, 0),
                preview=(EnvironmentOutputSegment(start_offset=0, data=preview),) if preview else (),
                reference=self.reference,
                available_end=self.stored,
                expires_at=None,
            )
        elif self.produced <= len(preview):
            capture = EnvironmentOutputCapture(
                kind="empty" if not preview else "inline",
                producer_complete=self.complete,
                content_complete=True,
                produced_bytes=self.produced,
                captured_bytes=len(preview),
                dropped_bytes=0,
                inline=preview,
                available_end=len(preview),
            )
        else:
            capture = EnvironmentOutputCapture(
                kind="truncated",
                producer_complete=self.complete,
                content_complete=False,
                produced_bytes=self.produced,
                captured_bytes=len(preview),
                dropped_bytes=max(self.produced - len(preview), 0),
                inline=preview,
                available_end=len(preview),
            )
        self.preview.clear()
        self.writer = None
        return capture

    def snapshot(self) -> EnvironmentOutputCapture:
        preview = bytes(self.preview)
        return EnvironmentOutputCapture(
            kind="empty" if not preview and self.complete else "inline" if self.complete else "truncated",
            producer_complete=self.complete,
            content_complete=self.complete and self.produced <= len(preview),
            produced_bytes=self.produced,
            captured_bytes=len(preview),
            dropped_bytes=max(self.produced - len(preview), 0),
            inline=preview,
            available_end=len(preview),
        )


@dataclass(slots=True)
class _WindowsTree:
    job: WindowsJob
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False


@dataclass(slots=True)
class _ProcessRecord:
    token: str
    handle: BoundProcessHandle
    process: asyncio.subprocess.Process
    started_at: datetime
    wall_time_seconds: float
    stdout: _OutputCollector
    stderr: _OutputCollector
    stdin_open: bool
    stdin_bytes: int
    stdin_limit: int | None
    stdin_lock: asyncio.Lock
    release_lock: asyncio.Lock
    status: ProcessStatus
    watcher: asyncio.Task[None] | None = None
    terminal: asyncio.Event = field(default_factory=asyncio.Event)
    output: ProcessOutputSnapshot | None = None


class LocalProcessManager:
    """Own every spawned native process tree until terminal cleanup and release."""

    def __init__(
        self,
        *,
        files: LocalFileOperator,
        retention: LocalRetentionStore,
        policy: _DirectLocalProcessPolicy,
        output_policy: _DirectLocalOutputPolicy,
        shell_profiles: tuple[DirectLocalShellProfile, ...],
        provider_type: str,
        environment_id: str,
        execution_id: str,
        generation: str,
    ) -> None:
        self._files = files
        self._retention = retention
        self._policy = policy
        self._output_policy = output_policy
        self._profiles = {
            profile.profile_id: profile.model_copy(
                update={"executable": _resolve_configured_executable(profile.executable)}
            )
            for profile in shell_profiles
        }
        self._allowed_executables = {_resolve_configured_executable(path) for path in policy.allowed_executables}
        self._provider_type = provider_type
        self._environment_id = environment_id
        self._execution_id = execution_id
        self._generation = generation
        self._records: dict[str, _ProcessRecord] = {}
        self._windows_trees: dict[int, _WindowsTree] = {}
        self._slots = asyncio.Semaphore(policy.max_concurrent_processes)
        self._operations = itertools.count(1)
        self._lock = asyncio.Lock()
        self._closed = False

    async def exec(self, request: CommandRequest) -> ShellExecResult:
        started = await self.start(request)
        handle = started.process.handle
        try:
            process = await self.wait(
                handle,
                condition="tree_cleaned",
                timeout_seconds=self._effective_wall_time(request) + self._policy.terminate_grace_seconds * 2 + 1,
            )
            assert process.output is not None
            assert process.output.stdout.producer_complete and process.output.stderr.producer_complete
            if request.output_policy.overflow == "fail" and any(
                capture.produced_bytes is not None and capture.produced_bytes > request.output_policy.max_output_bytes
                for capture in (process.output.stdout, process.output.stderr)
            ):
                raise EnvironmentError(
                    "Command output exceeds the requested projection limit.",
                    code="environment_too_large",
                )
            stdout, stderr = await asyncio.gather(
                materialize_capture(self._retention, process.output.stdout, request.output_policy),
                materialize_capture(self._retention, process.output.stderr, request.output_policy),
            )
            result = ShellExecResult(
                status=process.status,
                output=ProcessOutputSnapshot(stdout=stdout, stderr=stderr),
                receipt=self._receipt(),
            )
        except asyncio.CancelledError:
            try:
                await asyncio.shield(self.kill(handle))
            finally:
                record = self._record(handle)
                if record.process.returncode is not None:
                    await asyncio.shield(self._release_record(handle, release_outputs=True))
            raise
        except BaseException:
            record = self._record(handle)
            if record.process.returncode is not None:
                await self._release_record(handle, release_outputs=True)
            raise
        try:
            await self._release_record(handle, release_outputs=False)
        except asyncio.CancelledError:
            await asyncio.shield(self._release_record(handle, release_outputs=True))
            raise
        return result

    async def list(self, *, limit: int) -> ProcessDiscovery:
        raise EnvironmentError("Direct Local discovery is unsupported.", code="environment_unsupported")

    async def start(self, request: CommandRequest) -> ProcessStartResult:
        if os.name not in {"posix", "nt"}:
            raise EnvironmentError("Direct Local processes are unavailable on this OS.", code="environment_unsupported")
        self._validate_request(request)
        stdin_limit = self._effective_stdin_limit(request)
        await self._slots.acquire()
        process: asyncio.subprocess.Process | None = None
        record: _ProcessRecord | None = None
        stdout_collector: _OutputCollector | None = None
        stderr_collector: _OutputCollector | None = None
        ownership_committed = False
        try:
            async with self._lock:
                if self._closed:
                    raise EnvironmentError("Direct Local process manager is closed.", code="environment_closed")
            argv = await asyncio.to_thread(self._argv, request)
            cwd = await self._files.resolve_native_directory(request.cwd or "/")
            environment = os.environ.copy() if self._policy.inherit_environment else {}
            # Windows environment names are case-insensitive, including PATH/Path.
            for key in request.environment.unset:
                environment.pop(key.upper() if os.name == "nt" else key, None)
            environment.update(
                {(key.upper() if os.name == "nt" else key): value for key, value in request.environment.set.items()}
            )
            output_policy = request.output_policy
            stdout_collector = _OutputCollector(
                policy=output_policy,
                buffer_limit=self._output_policy.max_buffer_bytes,
                writer=await self._reserve_output(output_policy),
            )
            stderr_collector = _OutputCollector(
                policy=output_policy,
                buffer_limit=self._output_policy.max_buffer_bytes,
                writer=await self._reserve_output(output_policy),
            )
            try:
                spawn = asyncio.create_task(self._spawn(argv, cwd, environment))
                # Complete ownership transfer even under repeated cancellation.
                process, cancellation = await _settle_task(spawn)
                if cancellation is not None:
                    raise cancellation
            except OSError as exc:
                raise _environment_error_from_os(exc, action="start the configured executable") from exc
            assert process is not None
            token = token_hex(16)
            handle = BoundProcessHandle(
                execution_id=self._execution_id,
                identity=ProcessIdentity(
                    provider_type=self._provider_type,
                    environment_id=self._environment_id,
                    generation=self._generation,
                    process_id=token,
                ),
                observed_generation=self._generation,
                handle=OpaqueProcessHandle._from_payload(token),
            )
            started_at = datetime.now(UTC)
            record = _ProcessRecord(
                token=token,
                handle=handle,
                process=process,
                started_at=started_at,
                wall_time_seconds=self._effective_wall_time(request),
                stdout=stdout_collector,
                stderr=stderr_collector,
                stdin_open=process.stdin is not None,
                stdin_bytes=0,
                stdin_limit=stdin_limit,
                stdin_lock=asyncio.Lock(),
                release_lock=asyncio.Lock(),
                status=ProcessStatus(
                    phase="running",
                    started_at=started_at,
                    cleanup="pending",
                ),
            )
            async with self._lock:
                if self._closed:
                    raise EnvironmentError("Direct Local process manager is closed.", code="environment_closed")
                self._records[token] = record
            record.watcher = asyncio.create_task(self._watch(record))
            ownership_committed = True
            if request.initial_stdin:
                await self.write_stdin(handle, request.initial_stdin, close_after_write=not request.keep_stdin_open)
            elif not request.keep_stdin_open:
                await self.close_stdin(handle)
            return ProcessStartResult(process=self._info(record), receipt=self._receipt())
        except BaseException:
            if process is not None:
                await asyncio.shield(self._terminate_process(process))
            if ownership_committed and record is not None:
                if record.watcher is not None:
                    await asyncio.shield(record.watcher)
                if record.status.cleanup == "complete":
                    await self.release(record.handle)
            else:
                if stdout_collector is not None and stdout_collector.writer is not None:
                    await stdout_collector.writer.abort()
                if stderr_collector is not None and stderr_collector.writer is not None:
                    await stderr_collector.writer.abort()
                self._slots.release()
            raise

    async def _spawn(self, argv: tuple[str, ...], cwd: Path, environment: dict[str, str]) -> asyncio.subprocess.Process:
        job: WindowsJob | None = None
        process: asyncio.subprocess.Process | None = None
        if os.name == "nt":
            from .._windows_job import WindowsJob

            job = WindowsJob.create()
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=job is None,
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | job.creation_flags) if job is not None else 0,
            )
            if job is not None:
                job.assign_and_resume(process.pid)
                self._windows_trees[process.pid] = _WindowsTree(job)
            return process
        except BaseException:
            if process is not None:
                if process.returncode is None:
                    process.kill()
                if job is not None:
                    job.close()
                    job = None
                await process.wait()
            if job is not None:
                job.close()
            raise

    async def _reserve_output(self, policy: EnvironmentOutputPolicy) -> LocalRetentionWriter | None:
        if policy.overflow != "retain":
            return None
        return await self._retention.reserve(max_bytes=policy.max_output_bytes)

    def _validate_request(self, request: CommandRequest) -> None:
        if request.network == "deny":
            raise EnvironmentError("Direct Local cannot enforce network denial.", code="environment_unsupported")
        if request.limits.memory_bytes is not None or request.limits.cpu_time_seconds is not None:
            raise EnvironmentError(
                "Requested Direct Local resource limit is unsupported.", code="environment_unsupported"
            )
        if request.limits.process_count is not None:
            raise EnvironmentError(
                "Direct Local cannot enforce a process count ceiling.", code="environment_unsupported"
            )
        allowed_keys = self._policy.allowed_environment_keys
        if allowed_keys is not None:
            requested_keys = set(request.environment.set) | set(request.environment.unset)
            if os.name == "nt":
                allowed_keys = frozenset(key.upper() for key in allowed_keys)
                requested_keys = {key.upper() for key in requested_keys}
            if not requested_keys <= allowed_keys:
                raise EnvironmentError("Command environment key is not allowed.", code="environment_denied")
        stdin_limit = self._effective_stdin_limit(request)
        if request.initial_stdin is not None and stdin_limit is not None and len(request.initial_stdin) > stdin_limit:
            raise EnvironmentError("Initial stdin exceeds its finite limit.", code="environment_too_large")

    @staticmethod
    def _effective_stdin_limit(request: CommandRequest) -> int | None:
        return request.limits.stdin_bytes

    def _argv(self, request: CommandRequest) -> tuple[str, ...]:
        command = request.command
        if isinstance(command, ArgvCommand):
            executable = self._canonical_executable(command.executable)
            return (str(executable), *command.arguments)
        if not isinstance(command, ShellCommand):
            raise EnvironmentError("Command kind is unsupported.", code="environment_request_invalid")
        profile = self._profiles.get(command.profile_id)
        if profile is None:
            raise EnvironmentError("Shell profile is not allowed.", code="environment_denied")
        if command.login and not profile.allow_login:
            raise EnvironmentError("Shell login mode is not allowed.", code="environment_denied")
        executable = profile.executable
        if not executable.is_file():
            raise EnvironmentError("Shell profile executable is unavailable.", code="environment_not_found")
        if profile.dialect == "powershell":
            # EncodedCommand avoids Windows argv quoting and code-page ambiguity.
            prefix = (
                "$OutputEncoding = [Console]::InputEncoding = [Console]::OutputEncoding = "
                "[System.Text.UTF8Encoding]::new($false);\n"
            )
            encoded = base64.b64encode((prefix + command.script).encode("utf-16-le")).decode("ascii")
            return (str(executable), *profile.fixed_arguments, "-OutputFormat", "Text", "-EncodedCommand", encoded)
        login = ("-l",) if command.login else ()
        return (str(executable), *profile.fixed_arguments, *login, "-c", command.script)

    def _canonical_executable(self, value: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise EnvironmentError("Executable must be an absolute configured path.", code="environment_denied")
        try:
            canonical = path.resolve(strict=True)
        except OSError:
            raise EnvironmentError("Executable is unavailable.", code="environment_not_found") from None
        if canonical not in self._allowed_executables or not canonical.is_file():
            raise EnvironmentError("Executable is not allowed.", code="environment_denied")
        return canonical

    def _effective_wall_time(self, request: CommandRequest) -> float:
        requested = request.limits.wall_time_seconds
        return (
            min(requested, self._policy.max_wall_time_seconds)
            if requested is not None
            else self._policy.max_wall_time_seconds
        )

    async def _watch(self, record: _ProcessRecord) -> None:
        stdout = record.process.stdout
        stderr = record.process.stderr
        assert stdout is not None and stderr is not None
        stdout_task = asyncio.create_task(record.stdout.consume(stdout))
        stderr_task = asyncio.create_task(record.stderr.consume(stderr))
        wait_task = asyncio.create_task(self._wait_initial_exit(record.process))
        reason: Literal["exit", "signal", "timeout", "backend_lost"] = "exit"
        try:
            done, _ = await asyncio.wait(
                {wait_task},
                timeout=record.wall_time_seconds,
            )
            if wait_task not in done:
                reason = "timeout"
                await self._terminate_process(record.process)
                await wait_task
            return_code = record.process.returncode
            if return_code is not None and return_code < 0 and reason == "exit":
                reason = "signal"
            ended_at = datetime.now(UTC)
            if reason == "timeout":
                phase = "timed_out"
            elif return_code is not None and return_code < 0:
                phase = "signaled"
            else:
                phase = "exited"
            record.status = ProcessStatus(
                phase=phase,
                termination_reason=reason,
                exit_code=return_code if return_code is not None and return_code >= 0 else None,
                signal=_signal_name(return_code),
                started_at=record.started_at,
                ended_at=ended_at,
                cleanup="pending",
            )
            record.stdin_open = False
            record.terminal.set()
            await self._cleanup_tree(record.process.pid)
            await asyncio.gather(stdout_task, stderr_task)
            record.output = ProcessOutputSnapshot(
                stdout=await record.stdout.finish(),
                stderr=await record.stderr.finish(),
            )
            record.status = record.status.model_copy(update={"cleanup": "complete"})
        except BaseException:
            if record.process.returncode is None:
                await asyncio.shield(self._terminate_process(record.process))
            for task in (stdout_task, stderr_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            for collector in (record.stdout, record.stderr):
                try:
                    if collector.reference is not None:
                        await self._retention.release(reference=collector.reference)
                    elif collector.writer is not None:
                        await collector.writer.abort()
                except EnvironmentError:
                    pass
            record.status = ProcessStatus(
                phase="failed",
                termination_reason="backend_lost",
                started_at=record.started_at,
                ended_at=datetime.now(UTC),
                cleanup="failed",
            )
            raise
        finally:
            record.terminal.set()
            self._slots.release()

    @staticmethod
    async def _wait_initial_exit(process: asyncio.subprocess.Process) -> None:
        # asyncio Process.wait can also wait for inherited pipe closure. Native
        # returncode is the independent root-exit fact, even with live descendants.
        while process.returncode is None:
            await asyncio.sleep(0.01)

    async def rebind(
        self,
        identity: ProcessIdentity,
        *,
        output_policy: EnvironmentOutputPolicy,
    ) -> ProcessInfo:
        del output_policy
        if (
            identity.provider_type != self._provider_type
            or identity.environment_id != self._environment_id
            or identity.generation != self._generation
        ):
            raise EnvironmentError("Process identity belongs to another Environment.", code="environment_stale_mount")
        record = self._records.get(identity.process_id)
        if record is None:
            raise EnvironmentError("Process is unavailable.", code="environment_not_found")
        return self._info(record)

    async def inspect(self, handle: BoundProcessHandle) -> ProcessInfo:
        return self._info(self._record(handle))

    async def read_output(
        self,
        handle: BoundProcessHandle,
        *,
        stdout_cursor: BoundOutputCursor | None = None,
        stderr_cursor: BoundOutputCursor | None = None,
        stdout_start_offset: int | None = None,
        stderr_start_offset: int | None = None,
        wait_seconds: float = 0,
        policy: EnvironmentOutputPolicy,
    ) -> ProcessReadOutputResult:
        if not math.isfinite(wait_seconds) or wait_seconds < 0:
            raise EnvironmentError("Process output wait is invalid.", code="environment_request_invalid")
        record = self._record(handle)
        if wait_seconds and record.watcher is not None and not record.watcher.done():
            try:
                await asyncio.wait_for(asyncio.shield(record.watcher), timeout=wait_seconds)
            except TimeoutError:
                pass
        process = self._info(record)
        assert process.output is not None
        stdout, stderr = await asyncio.gather(
            self._read_stream(
                process.output.stdout,
                cursor=stdout_cursor,
                start_offset=stdout_start_offset,
                policy=policy,
            ),
            self._read_stream(
                process.output.stderr,
                cursor=stderr_cursor,
                start_offset=stderr_start_offset,
                policy=policy,
            ),
        )
        return ProcessReadOutputResult(process=process, stdout=stdout, stderr=stderr)

    async def _read_stream(
        self,
        capture: EnvironmentOutputCapture,
        *,
        cursor: BoundOutputCursor | None,
        start_offset: int | None,
        policy: EnvironmentOutputPolicy,
    ) -> ProcessStreamRead:
        if cursor is not None and start_offset is not None:
            raise EnvironmentError(
                "Output cursor and offset cannot both be selected.",
                code="environment_request_invalid",
            )
        selected_offset = start_offset or 0
        if policy.overflow == "fail" and capture.available_end - selected_offset > policy.max_output_bytes:
            raise EnvironmentError(
                "Process output exceeds the requested projection limit.",
                code="environment_too_large",
            )
        if capture.reference is not None:
            result = await self._retention.read(
                capture.reference,
                cursor=cursor,
                start_offset=start_offset,
                policy=policy,
            )
            return ProcessStreamRead(
                chunks=result.chunks,
                next_cursor=result.next_cursor,
                capture=capture,
            )
        if cursor is not None:
            raise EnvironmentError("Output cursor is unavailable for this stream.", code="environment_cursor_invalid")
        return _stream_read(capture, policy, start_offset=start_offset or 0)

    async def write_stdin(
        self,
        handle: BoundProcessHandle,
        data: bytes,
        *,
        close_after_write: bool = False,
    ) -> ProcessWriteStdinResult:
        record = self._record(handle)
        async with record.stdin_lock:
            if not record.stdin_open or record.process.stdin is None:
                raise EnvironmentError("Process stdin is closed.", code="environment_conflict")
            if record.stdin_limit is not None and record.stdin_bytes + len(data) > record.stdin_limit:
                raise EnvironmentError("Stdin write exceeds its finite limit.", code="environment_too_large")
            record.process.stdin.write(data)
            record.stdin_bytes += len(data)
            await record.process.stdin.drain()
            if close_after_write:
                await self._close_stdin(record)
            stdin_open = record.stdin_open
        return ProcessWriteStdinResult(
            accepted_bytes=len(data),
            stdin_open=stdin_open,
            receipt=self._receipt(),
        )

    async def close_stdin(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt:
        record = self._record(handle)
        async with record.stdin_lock:
            await self._close_stdin(record)
        return self._receipt()

    @staticmethod
    async def _close_stdin(record: _ProcessRecord) -> None:
        if record.stdin_open and record.process.stdin is not None:
            record.process.stdin.close()
            await record.process.stdin.wait_closed()
            record.stdin_open = False

    async def signal(
        self,
        handle: BoundProcessHandle,
        signal: Literal["interrupt", "terminate"],
    ) -> ProcessSignalResult:
        record = self._record(handle)
        accepted = record.process.returncode is None
        if os.name == "nt" and signal == "interrupt":
            raise EnvironmentError("Windows does not support portable tree interrupt.", code="environment_unsupported")
        if accepted:
            if os.name == "nt":
                await self._terminate_process(record.process)
            else:
                _signal_group(record.process.pid, os_signal.SIGINT if signal == "interrupt" else os_signal.SIGTERM)
        return ProcessSignalResult(accepted=accepted, process=self._info(record), receipt=self._receipt())

    async def wait(
        self,
        handle: BoundProcessHandle,
        *,
        condition: Literal["initial_terminal", "tree_cleaned"],
        timeout_seconds: float,
    ) -> ProcessInfo:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise EnvironmentError("Process wait timeout is invalid.", code="environment_request_invalid")
        record = self._record(handle)
        if record.watcher is not None:
            try:
                if condition == "initial_terminal":
                    await asyncio.wait_for(record.terminal.wait(), timeout=timeout_seconds)
                else:
                    await asyncio.wait_for(asyncio.shield(record.watcher), timeout=timeout_seconds)
            except TimeoutError as exc:
                raise EnvironmentError("Process wait timed out.", code="environment_timeout") from exc
        return self._info(record)

    async def kill(self, handle: BoundProcessHandle) -> ProcessControlResult:
        record = self._record(handle)
        await self._terminate_process(record.process)
        if record.watcher is not None:
            await asyncio.shield(record.watcher)
        return ProcessControlResult(process=self._info(record), receipt=self._receipt())

    async def release(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt:
        record = self._record(handle)
        if record.status.cleanup == "pending" or record.process.returncode is None:
            # Native scope ownership remains until adapter close; no Harness blanket kill.
            return self._receipt()
        return await self._release_record(handle, release_outputs=True)

    async def _release_record(
        self,
        handle: BoundProcessHandle,
        *,
        release_outputs: bool,
    ) -> EnvironmentOperationReceipt:
        record = self._record(handle)
        async with record.release_lock:
            if record.status.cleanup == "pending" or record.process.returncode is None:
                raise EnvironmentError("Active process cannot be released.", code="environment_conflict")
            if release_outputs and record.output is not None:
                for capture in (record.output.stdout, record.output.stderr):
                    if capture.reference is not None:
                        try:
                            await self._retention.release(reference=capture.reference)
                        except EnvironmentError as exc:
                            if exc.code != "environment_not_found":
                                raise
            async with self._lock:
                current = self._records.get(record.token)
                if current is not record:
                    raise EnvironmentError("Process handle is unavailable.", code="environment_not_found")
                self._records.pop(record.token)
            return self._receipt()

    def _record(self, handle: BoundProcessHandle) -> _ProcessRecord:
        if handle.execution_id != self._execution_id or handle.observed_generation != self._generation:
            raise EnvironmentError("Process handle is foreign or stale.", code="environment_stale_mount")
        token = _unwrap_opaque(handle.handle, OpaqueProcessHandle)
        record = self._records.get(token)
        if record is None:
            raise EnvironmentError("Process handle is unavailable.", code="environment_not_found")
        return record

    def _info(self, record: _ProcessRecord) -> ProcessInfo:
        output = record.output or ProcessOutputSnapshot(
            stdout=record.stdout.snapshot(),
            stderr=record.stderr.snapshot(),
        )
        return ProcessInfo(
            handle=record.handle,
            status=record.status,
            stdin_open=record.stdin_open,
            output=output,
        )

    async def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
        if os.name == "nt":
            await self._cleanup_tree(process.pid)
            await self._wait_initial_exit(process)
            return
        if process.returncode is not None:
            await self._cleanup_group(process.pid)
            return
        _signal_group(process.pid, os_signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=self._policy.terminate_grace_seconds)
        except TimeoutError:
            _signal_group(process.pid, os_signal.SIGKILL)
            await process.wait()
        await self._cleanup_group(process.pid)

    async def _cleanup_tree(self, process_id: int) -> None:
        if os.name != "nt":
            await self._cleanup_group(process_id)
            return
        tree = self._windows_trees.get(process_id)
        if tree is None:
            return
        async with tree.lock:
            if tree.closed:
                return
            cleanup = asyncio.create_task(
                asyncio.to_thread(
                    tree.job.terminate_and_wait,
                    timeout_seconds=self._policy.terminate_grace_seconds,
                    poll_seconds=0.01,
                )
            )
            try:
                _, cancellation = await _settle_task(cleanup)
                if cancellation is not None:
                    raise cancellation
            finally:
                tree.job.close()
                tree.closed = True
                self._windows_trees.pop(process_id, None)

    async def _cleanup_group(self, process_group: int) -> None:
        try:
            os.killpg(process_group, os_signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            # macOS can report EPERM when concurrent process.wait() reaps the
            # final member of a session-owned group. No signalable child remains.
            return
        await asyncio.sleep(self._policy.terminate_grace_seconds)
        try:
            os.killpg(process_group, os_signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def _receipt(self) -> EnvironmentOperationReceipt:
        return EnvironmentOperationReceipt(
            execution_id=self._execution_id,
            observed_generation=self._generation,
            operation_id=f"operation-{next(self._operations)}",
            stage="completed",
            outcome="succeeded",
        )

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            records = tuple(self._records.values())
        await asyncio.gather(
            *(self._terminate_process(record.process) for record in records if record.process.returncode is None),
            return_exceptions=True,
        )
        watchers = tuple(record.watcher for record in records if record.watcher is not None)
        if watchers:
            await asyncio.gather(*watchers, return_exceptions=True)
        for record in records:
            if record.process.returncode is not None:
                try:
                    await self.release(record.handle)
                except EnvironmentError:
                    pass


class LocalShell:
    """Internal Direct Local foreground shell facade."""

    def __init__(self, processes: LocalProcessManager) -> None:
        self._processes = processes

    async def exec(self, request: CommandRequest) -> ShellExecResult:
        return await self._processes.exec(request)


class LocalPortOperator:
    def __init__(self, policy: _DirectLocalPortPolicy) -> None:
        self._policy = policy

    async def inspect(self, target: PortTarget) -> PortObservation:
        self._validate(target)
        status: Literal["listening", "not_listening", "unknown"] = "not_listening"
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", target.port),
                timeout=1.0,
            )
            del reader
            writer.close()
            await writer.wait_closed()
            status = "listening"
        except (ConnectionError, OSError, TimeoutError):
            pass
        return PortObservation(
            target=target.model_copy(update={"alias": None}),
            status=status,
            observed_at=datetime.now(UTC),
        )

    async def wait(
        self,
        target: PortTarget,
        *,
        desired: Literal["listening", "not_listening"],
        timeout_seconds: float,
    ) -> PortObservation:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise EnvironmentError("Port wait timeout is invalid.", code="environment_request_invalid")
        async with asyncio.timeout(timeout_seconds):
            while True:
                observation = await self.inspect(target)
                if observation.status == desired:
                    return observation
                await asyncio.sleep(0.05)

    def _validate(self, target: PortTarget) -> None:
        if target.address != "loopback" or target.port not in self._policy.allowed_ports:
            raise EnvironmentError("Port target is not allowed.", code="environment_denied")


async def _settle_task[T](task: asyncio.Task[T]) -> tuple[T, asyncio.CancelledError | None]:
    """Finish an ownership transfer before allowing repeated cancellation through."""
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            return await asyncio.shield(task), cancellation
        except asyncio.CancelledError as exc:
            if task.cancelled():
                raise
            cancellation = cancellation or exc
        except BaseException as exc:
            if cancellation is not None:
                cancellation.add_note(f"Owned operation also failed: {exc!r}")
                raise cancellation from None
            raise


def _resolve_configured_executable(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise EnvironmentError("Configured executable must be absolute.", code="environment_request_invalid")
    try:
        canonical = expanded.resolve(strict=True)
    except (OSError, ValueError):
        raise EnvironmentError("Configured executable is unavailable.", code="environment_not_found") from None
    if not canonical.is_file():
        raise EnvironmentError("Configured executable is not a file.", code="environment_request_invalid")
    return canonical


def _signal_group(process_group: int, signal: int) -> None:
    try:
        os.killpg(process_group, signal)
    except ProcessLookupError:
        pass
    except OSError as exc:
        if exc.errno != errno.ESRCH:
            raise


def _environment_error_from_os(exc: OSError, *, action: str) -> EnvironmentError:
    if isinstance(exc, FileNotFoundError):
        code = "environment_not_found"
    elif isinstance(exc, PermissionError):
        code = "environment_denied"
    else:
        code = "environment_provider_failure"
    return EnvironmentError(f"Direct Local could not {action}.", code=code)


def _signal_name(return_code: int | None) -> Literal["interrupt", "terminate", "kill"] | None:
    if os.name != "posix":
        return None
    if return_code == -os_signal.SIGINT:
        return "interrupt"
    if return_code == -os_signal.SIGTERM:
        return "terminate"
    if return_code == -os_signal.SIGKILL:
        return "kill"
    return None


def _stream_read(
    capture: EnvironmentOutputCapture,
    policy: EnvironmentOutputPolicy,
    *,
    start_offset: int = 0,
) -> ProcessStreamRead:
    data = capture.inline
    if data is None and capture.preview:
        data = b"".join(segment.data for segment in capture.preview)
    available = data or b""
    if start_offset < 0 or start_offset > len(available):
        raise EnvironmentError("Output offset is unavailable for this stream.", code="environment_cursor_invalid")
    selected = available[start_offset : start_offset + policy.max_inline_bytes]
    chunks = (EnvironmentOutputSegment(start_offset=start_offset, data=selected),) if selected else ()
    return ProcessStreamRead(chunks=chunks, next_cursor=None, capture=capture)
