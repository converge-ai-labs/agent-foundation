"""Native E2B process handles with command-local byte capture."""

from __future__ import annotations

import asyncio
import math
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ..commands import (
    ArgvCommand,
    BoundProcessHandle,
    CommandRequest,
    ProcessControlResult,
    ProcessIdentity,
    ProcessInfo,
    ProcessOutputSnapshot,
    ProcessReadOutputResult,
    ProcessSignalResult,
    ProcessStartResult,
    ProcessStreamRead,
    ProcessWriteStdinResult,
    ShellExecResult,
)
from ..models import EnvironmentError, EnvironmentOperationReceipt
from ..retention import BoundOutputCursor, EnvironmentOutputPolicy, OpaqueProcessHandle, _unwrap_opaque
from .commands import GuestCommands
from .errors import sdk_errors
from .output import E2BOutputs, ProcessRecord, validate_process_id

if TYPE_CHECKING:
    from e2b.sandbox_async.commands.command_handle import AsyncCommandHandle


@dataclass(slots=True)
class _Process:
    policy: EnvironmentOutputPolicy
    native: AsyncCommandHandle | None = None


class E2BProcesses:
    def __init__(self, commands: GuestCommands, outputs: E2BOutputs, environment_id: str, owner: str) -> None:
        self.commands = commands
        self.outputs = outputs
        self.environment_id = environment_id
        self._processes: dict[str, _Process] = {}
        self._lock = asyncio.Lock()
        self._owner = owner

    async def start(self, request: CommandRequest) -> ProcessStartResult:
        config = self.commands.configuration
        if request.network == "deny" and config.allow_internet_access:
            raise EnvironmentError("Per-command network denial is unsupported.", code="environment_unsupported")
        if any(
            value is not None
            for value in (request.limits.process_count, request.limits.memory_bytes, request.limits.cpu_time_seconds)
        ):
            raise EnvironmentError("Per-command resource limits are unsupported.", code="environment_unsupported")
        command = request.command
        if isinstance(command, ArgvCommand):
            argv = [command.executable, *command.arguments]
        else:
            if command.profile_id != "default":
                raise EnvironmentError("Unknown E2B shell profile.", code="environment_unsupported")
            argv = [config.shell, *(["-l"] if command.login else []), "-c", command.script]
        cwd = (await self.commands.files("resolve", {"path": request.cwd or "/"}))["path"]
        token = f"process-{secrets.token_hex(12)}"
        stdin_limit = min(request.limits.stdin_bytes or 1024 * 1024, 1024 * 1024)
        if len(request.initial_stdin or b"") > stdin_limit:
            raise EnvironmentError("Initial stdin exceeds its limit.", code="environment_too_large")
        plan = {
            "owner": self._owner,
            "boot_id": self.commands.boot_id,
            "directory": f"{self.commands.root}/{token}",
            "max_processes": config.max_processes,
            "argv": argv,
            "cwd": cwd,
            "environment": request.environment.model_dump(mode="json"),
            "wall_time_seconds": min(
                request.limits.wall_time_seconds or config.max_wall_time_seconds, config.max_wall_time_seconds
            ),
            "max_output_bytes": min(request.output_policy.max_output_bytes, config.max_output_bytes),
            "overflow": request.output_policy.overflow,
            "stdin_bytes": stdin_limit,
            "keep_stdin_open": request.keep_stdin_open or bool(request.initial_stdin),
        }
        async with self._lock:
            if len(self._processes) >= config.max_processes:
                raise EnvironmentError("E2B process capacity is exhausted.", code="environment_busy")
            record = _Process(request.output_policy)
            self._processes[token] = record
        try:
            with sdk_errors(mutation=True):
                record.native = await self.commands.sandbox.commands.run(
                    self.commands.command("runner", plan),
                    background=True,
                    stdin=bool(plan["keep_stdin_open"]),
                    user=config.user,
                    timeout=0,
                    request_timeout=config.request_timeout_seconds,
                )
            handle = self._handle(token)
            async with asyncio.timeout(config.request_timeout_seconds):
                while True:
                    try:
                        state = await self.outputs.record(token)
                        if state.phase != "starting":
                            break
                    except EnvironmentError as error:
                        if error.code != "environment_not_found":
                            raise
                        if record.native.exit_code is not None:
                            self._processes.pop(token, None)
                            raise EnvironmentError("E2B command admission failed.", code="environment_busy") from None
                    await asyncio.sleep(0.05)
            if request.initial_stdin:
                await self.write_stdin(handle, request.initial_stdin, close_after_write=not request.keep_stdin_open)
            return ProcessStartResult(process=await self._info(token, state), receipt=self.commands.receipt())
        except BaseException:
            # Keep the correlation after uncertain dispatch; close can inspect it, and the guest deadline remains effective.
            if record.native is None:
                self._processes.pop(token, None)
            raise

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
            raise EnvironmentError("E2B process is unavailable.", code="environment_not_found")
        return token, record

    async def _info(self, token: str, state: ProcessRecord | None = None) -> ProcessInfo:
        state = state or await self.outputs.record(token)
        policy = self._processes[token].policy
        stdout, stderr = await asyncio.gather(
            self.outputs.capture(token, "stdout", state, policy), self.outputs.capture(token, "stderr", state, policy)
        )
        return ProcessInfo(
            handle=self._handle(token),
            status=state.status,
            stdin_open=state.stdin_open,
            output=ProcessOutputSnapshot(stdout=stdout, stderr=stderr),
        )

    async def inspect(self, handle: BoundProcessHandle) -> ProcessInfo:
        token, _ = self._resolve(handle)
        return await self._info(token)

    async def rebind(self, identity: ProcessIdentity, *, output_policy: EnvironmentOutputPolicy) -> ProcessInfo:
        validate_process_id(identity.process_id)
        if identity != self._handle(identity.process_id).identity:
            raise EnvironmentError("E2B process identity is stale.", code="environment_stale_mount")
        state = ProcessRecord.model_validate(await self.commands.process("rebind", {"process_id": identity.process_id}))
        self._processes.setdefault(identity.process_id, _Process(output_policy))
        return await self._info(identity.process_id, state)

    async def _native(self, token: str, record: _Process) -> AsyncCommandHandle:
        if record.native is None:
            state = await self.outputs.record(token)
            if state.terminal:
                raise EnvironmentError("E2B process has exited.", code="environment_conflict")
            with sdk_errors():
                record.native = await self.commands.sandbox.commands.connect(
                    state.runner_pid, timeout=0, request_timeout=self.commands.configuration.request_timeout_seconds
                )
        return record.native

    async def write_stdin(
        self, handle: BoundProcessHandle, data: bytes, *, close_after_write: bool = False
    ) -> ProcessWriteStdinResult:
        token, record = self._resolve(handle)
        native = await self._native(token, record)
        # Reserve once across all adapters; uncertain sends consume quota rather than permitting an overrun.
        await self.commands.process("reserve_stdin", {"process_id": token, "bytes": len(data)}, mutation=True)
        with sdk_errors(mutation=True):
            await native.send_stdin(data, request_timeout=self.commands.configuration.request_timeout_seconds)
        if close_after_write:
            await self.close_stdin(handle)
        return ProcessWriteStdinResult(
            accepted_bytes=len(data), stdin_open=not close_after_write, receipt=self.commands.receipt()
        )

    async def close_stdin(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt:
        token, record = self._resolve(handle)
        native = await self._native(token, record)
        with sdk_errors(mutation=True):
            await native.close_stdin(request_timeout=self.commands.configuration.request_timeout_seconds)
        return self.commands.receipt()

    async def signal(
        self, handle: BoundProcessHandle, signal: Literal["interrupt", "terminate"]
    ) -> ProcessSignalResult:
        token, _ = self._resolve(handle)
        await self.commands.process("signal", {"process_id": token, "signal": signal}, mutation=True)
        return ProcessSignalResult(accepted=True, process=await self.inspect(handle), receipt=self.commands.receipt())

    async def kill(self, handle: BoundProcessHandle) -> ProcessControlResult:
        token, _ = self._resolve(handle)
        await self.commands.process("signal", {"process_id": token, "signal": "kill"}, mutation=True)
        state = await self.wait(handle, condition="initial_terminal", timeout_seconds=10)
        return ProcessControlResult(process=state, receipt=self.commands.receipt())

    async def wait(
        self,
        handle: BoundProcessHandle,
        *,
        condition: Literal["initial_terminal", "tree_cleaned"],
        timeout_seconds: float,
    ) -> ProcessInfo:
        if condition not in {"initial_terminal", "tree_cleaned"}:
            raise EnvironmentError("Unknown E2B wait condition.", code="environment_request_invalid")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise EnvironmentError("Invalid E2B wait timeout.", code="environment_request_invalid")
        token, _ = self._resolve(handle)
        try:
            async with asyncio.timeout(timeout_seconds):
                while True:
                    state = await self.outputs.record(token)
                    if state.terminal:
                        return await self._info(token, state)
                    await asyncio.sleep(0.1)
        except TimeoutError:
            raise EnvironmentError("E2B wait timed out.", code="environment_timeout") from None

    async def exec(self, request: CommandRequest) -> ShellExecResult:
        result = await self.start(request)
        handle = result.process.handle
        try:
            process = await self.wait(
                handle,
                condition="initial_terminal",
                timeout_seconds=self.commands.configuration.max_wall_time_seconds + 10,
            )
            result = ShellExecResult(status=process.status, output=process.output, receipt=self.commands.receipt())
        except BaseException as error:
            try:
                await asyncio.shield(self.kill(handle))
                await asyncio.shield(self.release(handle))
            except Exception:
                error.add_note("E2B command cleanup failed; the sandbox deadline remains effective.")
            raise
        await self.release(handle)
        return result

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
        token, _ = self._resolve(handle)
        if not math.isfinite(wait_seconds) or wait_seconds < 0:
            raise EnvironmentError("Invalid E2B output wait.", code="environment_request_invalid")
        if wait_seconds:
            await asyncio.sleep(min(wait_seconds, self.commands.configuration.request_timeout_seconds))
        stdout, stderr = await asyncio.gather(
            self.outputs.read(
                self.outputs.reference(token, "stdout"),
                cursor=stdout_cursor,
                start_offset=stdout_start_offset,
                policy=policy,
            ),
            self.outputs.read(
                self.outputs.reference(token, "stderr"),
                cursor=stderr_cursor,
                start_offset=stderr_start_offset,
                policy=policy,
            ),
        )
        state = await self.outputs.record(token)
        return ProcessReadOutputResult(
            process=await self._info(token, state),
            stdout=ProcessStreamRead(chunks=stdout.chunks, next_cursor=stdout.next_cursor, capture=stdout.capture),
            stderr=ProcessStreamRead(chunks=stderr.chunks, next_cursor=stderr.next_cursor, capture=stderr.capture),
        )

    async def release(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt:
        token, record = self._resolve(handle)
        state = await self.outputs.record(token)
        if not state.terminal:
            raise EnvironmentError("Cannot release a running E2B process.", code="environment_conflict")
        if record.native is not None:
            await record.native.disconnect()
        await self.commands.process("release_process", {"process_id": token}, mutation=True)
        self._processes.pop(token)
        return self.commands.receipt()

    async def disconnect(self) -> None:
        handles = [record.native for record in self._processes.values() if record.native is not None]
        try:
            await asyncio.gather(*(handle.disconnect() for handle in handles))
        finally:
            self._processes.clear()

    async def close(self) -> None:
        failures: list[Exception] = []
        try:
            owned = await self.commands.process("owned", {"owner": self._owner})
            tokens = owned.get("process_ids")
            if not isinstance(tokens, list) or not all(isinstance(token, str) for token in tokens):
                raise EnvironmentError("E2B ownership response is invalid.", code="environment_provider_failure")
            for token in tokens:
                assert isinstance(token, str)
                try:
                    state = await self.outputs.record(token)
                    if not state.terminal:
                        await self.commands.process("signal", {"process_id": token, "signal": "kill"}, mutation=True)
                        async with asyncio.timeout(10):
                            while not (await self.outputs.record(token)).terminal:
                                await asyncio.sleep(0.1)
                    await self.commands.process("release", {"process_id": token}, mutation=True)
                except EnvironmentError as error:
                    if error.code != "environment_not_found":
                        failures.append(error)
                except Exception as error:
                    failures.append(error)
        finally:
            await self.disconnect()
        if failures:
            raise ExceptionGroup("E2B process cleanup failed", failures)
