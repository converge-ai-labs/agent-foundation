"""Best-effort native E2B commands with Run-local bounded observations."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import Literal

from ..commands import (
    BoundProcessHandle,
    CommandRequest,
    ProcessControlResult,
    ProcessDiscovery,
    ProcessIdentity,
    ProcessInfo,
    ProcessOutputSnapshot,
    ProcessReadOutputResult,
    ProcessSignalResult,
    ProcessStartResult,
    ProcessStatus,
    ProcessWriteStdinResult,
    ShellCommand,
    ShellExecResult,
)
from ..models import EnvironmentError, EnvironmentOperationReceipt
from ..retention import BoundOutputCursor, EnvironmentOutputPolicy, OpaqueProcessHandle, _unwrap_opaque
from .commands import GuestCommands
from .errors import sdk_errors
from .output import CommandObservation, ObservationPool

_MAX_PROCESS_RECORDS = 100_000


@dataclass(slots=True)
class _Process:
    stdin_open: bool | None = None
    observation: CommandObservation | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class E2BProcesses:
    def __init__(self, commands: GuestCommands, environment_id: str) -> None:
        self.commands = commands
        self.environment_id = environment_id
        self._processes: dict[str, _Process] = {}
        self._lock = asyncio.Lock()
        config = commands.configuration
        self._observations = ObservationPool(config.max_active_observations, config.max_retained_output_bytes)

    def _check_record_capacity(self, additional: int) -> None:
        if len(self._processes) + additional > _MAX_PROCESS_RECORDS:
            raise EnvironmentError("E2B process reference capacity is exhausted.", code="environment_limit_exceeded")

    def _allocate(self, *, reattached: bool = False) -> CommandObservation:
        config = self.commands.configuration
        observation = CommandObservation(
            limit=config.max_observation_bytes, pool=self._observations, reason="reattached" if reattached else None
        )
        self._observations.reserve(observation)
        return observation

    async def start(self, request: CommandRequest) -> ProcessStartResult:
        config = self.commands.configuration
        command = request.command
        if not isinstance(command, ShellCommand) or command.profile_id != "default" or command.login is False:
            raise EnvironmentError("E2B supports its native login Bash shell only.", code="environment_unsupported")
        if request.environment.unset or any(value is not None for value in request.limits.model_dump().values()):
            raise EnvironmentError("E2B cannot enforce these command guarantees.", code="environment_unsupported")
        if request.network == "deny" and config.allow_internet_access:
            raise EnvironmentError("Per-command network denial is unsupported.", code="environment_unsupported")
        if len(request.initial_stdin or b"") > 1024 * 1024:
            raise EnvironmentError("Initial stdin exceeds its request limit.", code="environment_too_large")
        cwd = (await self.commands.files("resolve", {"path": request.cwd or "/"}))["path"]
        if not isinstance(cwd, str):
            raise EnvironmentError("E2B returned an invalid working directory.", code="environment_provider_failure")
        async with self._lock:
            self._check_record_capacity(1)
            observation = self._allocate()
            try:
                with sdk_errors(mutation=True):
                    native = await self.commands.sandbox.commands.run(
                        command.script,
                        background=True,
                        stdin=request.keep_stdin_open or bool(request.initial_stdin),
                        cwd=cwd,
                        envs=dict(request.environment.set),
                        user=config.user,
                        on_stdout=lambda text: observation.append("stdout", text),
                        on_stderr=lambda text: observation.append("stderr", text),
                        timeout=0,
                        request_timeout=config.request_timeout_seconds,
                    )
            except BaseException:
                self._observations.release(observation)
                raise
            token = str(native.pid)
            record = _Process(
                stdin_open=request.keep_stdin_open or bool(request.initial_stdin), observation=observation
            )
            self._processes[token] = record
            observation.attach(native)
        handle = self._handle(token)
        if request.initial_stdin:
            await self.write_stdin(handle, request.initial_stdin, close_after_write=not request.keep_stdin_open)
        return ProcessStartResult(
            process=self._info(token, record, ProcessStatus(phase="running")), receipt=self.commands.receipt()
        )

    def _handle(self, token: str) -> BoundProcessHandle:
        return BoundProcessHandle(
            mount_id=self.commands.mount_id,
            observed_generation=self.commands.generation,
            identity=ProcessIdentity(
                provider_type="a13n.e2b",
                environment_id=self.environment_id,
                generation=self.commands.generation,
                process_id=token,
            ),
            handle=OpaqueProcessHandle._from_payload(token),
        )

    def _resolve(self, handle: BoundProcessHandle) -> tuple[str, _Process]:
        token = _unwrap_opaque(handle.handle, OpaqueProcessHandle)
        if handle != self._handle(token):
            raise EnvironmentError("E2B process handle is foreign or stale.", code="environment_stale_mount")
        record = self._processes.get(token)
        if record is None:
            raise EnvironmentError("E2B observation is unavailable.", code="environment_not_found")
        return token, record

    def _info(self, token: str, record: _Process, status: ProcessStatus) -> ProcessInfo:
        observation = record.observation
        if observation is not None and observation.terminal is not None:
            status = observation.terminal
        return ProcessInfo(
            handle=self._handle(token),
            status=status,
            stdin_open=False if status.phase == "exited" else record.stdin_open,
        )

    async def list(self, *, limit: int) -> ProcessDiscovery:
        if limit <= 0 or limit > 1000:
            raise EnvironmentError("Invalid process list limit.", code="environment_request_invalid")
        with sdk_errors():
            native = await self.commands.sandbox.commands.list(
                request_timeout=self.commands.configuration.request_timeout_seconds
            )
        # The SDK does not paginate. Never project envs, native PIDs or arbitrary argv.
        selected = [process for process in native if process.tag != "pty"]
        async with self._lock:
            self._check_record_capacity(sum(str(process.pid) not in self._processes for process in selected[:limit]))
            infos = []
            for process in selected[:limit]:
                token = str(process.pid)
                record = self._processes.setdefault(token, _Process())
                infos.append(self._info(token, record, ProcessStatus(phase="running")))
        return ProcessDiscovery(processes=tuple(infos), has_more=len(selected) > limit)

    async def inspect(self, handle: BoundProcessHandle) -> ProcessInfo:
        token, record = self._resolve(handle)
        if record.observation is not None and record.observation.terminal is not None:
            return self._info(token, record, record.observation.terminal)
        with sdk_errors():
            native = await self.commands.sandbox.commands.list(
                request_timeout=self.commands.configuration.request_timeout_seconds
            )
        phase = "running" if any(str(process.pid) == token for process in native) else "missing"
        if phase == "missing" and record.observation is not None and not record.observation.closed:
            # A native end event can still be in flight after disappearance from the running inventory.
            phase = "unknown"
        status = ProcessStatus(phase=phase)
        return self._info(token, record, status)

    async def rebind(self, identity: ProcessIdentity, *, output_policy: EnvironmentOutputPolicy) -> ProcessInfo:
        del output_policy
        if not identity.process_id.isdecimal() or identity != self._handle(identity.process_id).identity:
            raise EnvironmentError("E2B process identity is foreign or stale.", code="environment_stale_mount")
        async with self._lock:
            self._check_record_capacity(int(identity.process_id not in self._processes))
            self._processes.setdefault(identity.process_id, _Process())
        return await self.inspect(self._handle(identity.process_id))

    async def _observe(self, token: str, record: _Process) -> CommandObservation:
        async with record.lock:
            observation = record.observation
            if observation is not None:
                if observation.terminal is not None or observation.capped.is_set() or not observation.closed:
                    return observation
                await observation.disconnect()
                self._observations.reserve(observation)
                if observation.reason != "observation_evicted":
                    observation.reason = "reattached"
            else:
                observation = self._allocate(reattached=True)
                record.observation = observation
            try:
                with sdk_errors():
                    native = await self.commands.sandbox.commands.connect(
                        int(token),
                        on_stdout=lambda text: observation.append("stdout", text),
                        on_stderr=lambda text: observation.append("stderr", text),
                        timeout=0,
                        request_timeout=self.commands.configuration.request_timeout_seconds,
                    )
            except BaseException:
                self._observations.finish(observation)
                if observation.reason != "observation_evicted":
                    observation.reason = "connection_lost"
                raise
            observation.attach(native)
            return observation

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
        if stdout_cursor is not None or stderr_cursor is not None:
            raise EnvironmentError("E2B observations use explicit offsets.", code="environment_cursor_invalid")
        token, record = self._resolve(handle)
        info = await self.inspect(handle)
        if info.status.phase != "missing":
            observation = await self._observe(token, record)
        else:
            observation = record.observation
            if observation is None:
                observation = CommandObservation(
                    limit=self.commands.configuration.max_observation_bytes,
                    pool=self._observations,
                    reason="connection_lost",
                )
        if wait_seconds > 0 and not observation.closed:
            try:
                async with asyncio.timeout(wait_seconds):
                    await observation.changed.wait()
            except TimeoutError:
                pass
        return ProcessReadOutputResult(
            process=info,
            stdout=observation.read("stdout", stdout_start_offset or 0, policy),
            stderr=observation.read("stderr", stderr_start_offset or 0, policy),
        )

    async def wait(
        self,
        handle: BoundProcessHandle,
        *,
        condition: Literal["initial_terminal", "tree_cleaned"],
        timeout_seconds: float,
    ) -> ProcessInfo:
        if condition != "initial_terminal":
            raise EnvironmentError("E2B cannot verify process-tree cleanup.", code="environment_unsupported")
        if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
            raise EnvironmentError("Invalid wait timeout.", code="environment_request_invalid")
        token, record = self._resolve(handle)
        observation = record.observation
        if observation is not None and observation.terminal is not None:
            return self._info(token, record, observation.terminal)
        if timeout_seconds == 0:
            # One native status poll, bounded by the configured request timeout.
            return await self.inspect(handle)
        info = self._info(token, record, ProcessStatus(phase="unknown"))
        loop = asyncio.get_running_loop()
        try:
            # The budget includes native inspection and stream attachment, not
            # just waiting for output. Output closure is not process completion.
            async with asyncio.timeout(timeout_seconds):
                info = await self.inspect(handle)
                if info.status.phase in {"exited", "missing"}:
                    return info
                observation = await self._observe(token, record)
                next_poll = loop.time() + 0.1
                while True:
                    if observation.terminal is not None:
                        return self._info(token, record, observation.terminal)
                    delay = next_poll - loop.time()
                    if delay <= 0:
                        info = await self.inspect(handle)
                        if info.status.phase in {"exited", "missing"}:
                            return info
                        next_poll = loop.time() + 0.1
                        continue
                    if observation.closed:
                        await asyncio.sleep(delay)
                    else:
                        observation.changed.clear()
                        try:
                            async with asyncio.timeout(delay):
                                await observation.changed.wait()
                        except TimeoutError:
                            pass
        except TimeoutError:
            if observation is not None and observation.terminal is not None:
                return self._info(token, record, observation.terminal)
            return info

    async def write_stdin(
        self, handle: BoundProcessHandle, data: bytes, *, close_after_write: bool = False
    ) -> ProcessWriteStdinResult:
        token, record = self._resolve(handle)
        if len(data) > 1024 * 1024:
            raise EnvironmentError("Stdin request is too large.", code="environment_too_large")
        with sdk_errors(mutation=True):
            await self.commands.sandbox.commands.send_stdin(
                int(token), data, request_timeout=self.commands.configuration.request_timeout_seconds
            )
        if close_after_write:
            await self.close_stdin(handle)
        return ProcessWriteStdinResult(
            accepted_bytes=len(data), stdin_open=record.stdin_open, receipt=self.commands.receipt()
        )

    async def close_stdin(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt:
        token, record = self._resolve(handle)
        with sdk_errors(mutation=True):
            await self.commands.sandbox.commands.close_stdin(
                int(token), request_timeout=self.commands.configuration.request_timeout_seconds
            )
        record.stdin_open = False
        return self.commands.receipt()

    async def signal(
        self, handle: BoundProcessHandle, signal: Literal["interrupt", "terminate"]
    ) -> ProcessSignalResult:
        self._resolve(handle)
        raise EnvironmentError("E2B supports native kill only.", code="environment_unsupported")

    async def kill(self, handle: BoundProcessHandle) -> ProcessControlResult:
        token, record = self._resolve(handle)
        with sdk_errors(mutation=True):
            accepted = await self.commands.sandbox.commands.kill(
                int(token), request_timeout=self.commands.configuration.request_timeout_seconds
            )
        if not accepted:
            raise EnvironmentError("Native command is missing.", code="environment_not_found")
        # Native acceptance is known; later status lookup is an independent observation.
        return ProcessControlResult(
            process=self._info(token, record, ProcessStatus(phase="unknown")), receipt=self.commands.receipt()
        )

    async def release(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt:
        token, record = self._resolve(handle)
        async with record.lock:
            if record.observation is not None:
                await record.observation.disconnect()
                self._observations.release(record.observation)
            self._processes.pop(token, None)
        return self.commands.receipt()

    async def disconnect(self) -> None:
        for record in self._processes.values():
            if record.observation is not None:
                await record.observation.disconnect()
                if (
                    record.observation.reason not in {"observation_limit", "observation_evicted"}
                    and record.observation.terminal is None
                ):
                    record.observation.reason = "connection_lost"

    async def close(self) -> None:
        await self.disconnect()
        for record in self._processes.values():
            if record.observation is not None:
                self._observations.release(record.observation)
        self._processes.clear()

    async def exec(self, request: CommandRequest) -> ShellExecResult:
        started = await self.start(request)
        handle = started.process.handle
        try:
            while True:
                info = await self.wait(handle, condition="initial_terminal", timeout_seconds=1)
                _, record = self._resolve(handle)
                observation = record.observation
                assert observation is not None
                if info.status.phase != "running":
                    stdout = observation.materialize("stdout", request.output_policy)
                    stderr = observation.materialize("stderr", request.output_policy)
                    return ShellExecResult(
                        status=info.status,
                        output=ProcessOutputSnapshot(stdout=stdout, stderr=stderr),
                        receipt=started.receipt,
                    )
        finally:
            await self.release(handle)
