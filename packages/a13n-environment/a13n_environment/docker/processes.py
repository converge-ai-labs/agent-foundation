"""Docker exec commands with bounded, Run-local output and explicit controls."""

from __future__ import annotations

import asyncio
import json
import math
import re
import socket
from typing import Literal
from uuid import uuid4

from .._local_retention import LocalRetentionStore
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
from .commands import DockerCommands
from .errors import engine_errors
from .observation import ByteBudget, ProcessObservation, StreamObservation, attach

# A command launcher, not a resident agent. Docker owns the resulting exec.
_LAUNCH = """import json,os,sys
r=json.loads(sys.argv[1])
if os.getpgrp()!=os.getpid(): os.setsid()
with open('/tmp/a13n/'+r['tag'],'w') as f: json.dump([os.getpid(),open('/proc/self/stat').read().rsplit(')',1)[1].split()[19]],f)
for key in r['unset']: os.environ.pop(key,None)
os.execvpe(r['argv'][0],r['argv'],os.environ)
"""


class DockerProcesses:
    def __init__(self, commands: DockerCommands, environment_id: str, retention: LocalRetentionStore) -> None:
        self.commands = commands
        self.environment_id = environment_id
        self.retention = retention
        self.records: dict[str, ProcessObservation] = {}
        self.budget = ByteBudget(commands.config.max_spool_bytes)
        self._lock = asyncio.Lock()

    def handle(self, token: str) -> BoundProcessHandle:
        generation = self.commands.container_id
        return BoundProcessHandle(
            execution_id=self.commands.execution_id,
            observed_generation=generation,
            identity=ProcessIdentity(
                provider_type="docker", environment_id=self.environment_id, generation=generation, process_id=token
            ),
            handle=OpaqueProcessHandle._from_payload(token),
        )

    def resolve(self, handle: BoundProcessHandle) -> str:
        self.commands.check_open()
        token = _unwrap_opaque(handle.handle, OpaqueProcessHandle)
        if not re.fullmatch(r"[0-9a-f]{64}", token) or handle != self.handle(token):
            raise EnvironmentError("Docker process handle is foreign or stale", code="environment_stale_mount")
        return token

    async def start(self, request: CommandRequest) -> ProcessStartResult:
        self.commands.check_open()
        config = self.commands.config
        if any(value is not None for value in request.limits.model_dump().values()):
            raise EnvironmentError(
                "Per-command resource limits are unsupported; configure container limits",
                code="environment_unsupported",
            )
        if request.network == "deny" and not config.disable_network:
            raise EnvironmentError("Network denial must be configured on the container", code="environment_unsupported")
        if len(request.initial_stdin or b"") > 1048576:
            raise EnvironmentError("Initial stdin is too large", code="environment_too_large")
        command = request.command
        if isinstance(command, ShellCommand):
            if command.profile_id != "default":
                raise EnvironmentError("Unknown Docker shell profile", code="environment_unsupported")
            argv = [config.shell, "-lc" if command.login else "-c", command.script]
        else:
            argv = [command.executable, *command.arguments]
        cwd = request.cwd or "/workspace"
        if not cwd.startswith("/"):
            cwd = "/workspace/" + cwd
        stdin = request.keep_stdin_open or bool(request.initial_stdin)
        async with self._lock:
            with engine_errors(mutation=True):
                if len(self.records) >= config.max_concurrent_processes:
                    raise EnvironmentError(
                        "Release process observations before starting more commands", code="environment_limit_exceeded"
                    )
                api = self.commands.engine.client.api
                result = await asyncio.to_thread(
                    api.exec_create,
                    self.commands.container_id,
                    [
                        config.python,
                        "-I",
                        "-c",
                        _LAUNCH,
                        json.dumps({"argv": argv, "unset": request.environment.unset, "tag": uuid4().hex}),
                    ],
                    stdin=stdin,
                    stdout=True,
                    stderr=True,
                    tty=False,
                    environment=dict(request.environment.set),
                    workdir=cwd,
                    user=config.user or "",
                )
                token = result["Id"]
                connection = await attach(api, token)
                record = ProcessObservation(
                    connection,
                    StreamObservation(config.max_output_bytes_per_stream, self.budget),
                    StreamObservation(config.max_output_bytes_per_stream, self.budget),
                    stdin,
                )
                self.records[token] = record
                record.task = asyncio.create_task(record.consume(), name="docker-exec-observation")
        handle = self.handle(token)
        if request.initial_stdin:
            await self.write_stdin(handle, request.initial_stdin, close_after_write=not request.keep_stdin_open)
        return ProcessStartResult(process=await self.inspect(handle), receipt=self.commands.receipt())

    async def inspect(self, handle: BoundProcessHandle) -> ProcessInfo:
        from docker.errors import NotFound

        token = self.resolve(handle)
        with engine_errors():
            try:
                native = await asyncio.to_thread(self.commands.engine.client.api.exec_inspect, token)
            except NotFound:
                return ProcessInfo(handle=handle, status=ProcessStatus(phase="missing"), stdin_open=False)
        if native["ContainerID"] != self.commands.container_id:
            raise EnvironmentError("Docker exec belongs to another container", code="environment_stale_mount")
        running = native["Running"]
        status = ProcessStatus(
            phase="running" if running else "starting" if native["ExitCode"] is None else "exited",
            exit_code=None if running else native["ExitCode"],
        )
        record = self.records.get(token)
        return ProcessInfo(handle=handle, status=status, stdin_open=record.stdin_open if running and record else False)

    async def list(self, *, limit: int) -> ProcessDiscovery:
        self.commands.check_open()
        if not 0 < limit <= 1000:
            raise EnvironmentError("Invalid process limit", code="environment_request_invalid")
        with engine_errors():
            container = await asyncio.to_thread(self.commands.engine.client.containers.get, self.commands.container_id)
        tokens = container.attrs.get("ExecIDs") or []
        infos = [await self.inspect(self.handle(token)) for token in tokens[:limit]]
        return ProcessDiscovery(processes=tuple(infos), has_more=len(tokens) > limit)

    async def rebind(self, identity: ProcessIdentity, *, output_policy: EnvironmentOutputPolicy) -> ProcessInfo:
        handle = self.handle(identity.process_id)
        if identity != handle.identity:
            raise EnvironmentError("Docker process identity is stale", code="environment_stale_mount")
        return await self.inspect(handle)

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
            raise EnvironmentError("Docker process output uses offsets", code="environment_cursor_invalid")
        if not math.isfinite(wait_seconds) or wait_seconds < 0:
            raise EnvironmentError("Invalid output wait", code="environment_request_invalid")
        token = self.resolve(handle)
        record = self.records.get(token)
        if record is None:
            raise EnvironmentError("Docker exec output is no longer attached", code="environment_not_found")
        record.changed.clear()
        if wait_seconds and not record.stdout.closed:
            try:
                async with asyncio.timeout(wait_seconds):
                    await record.changed.wait()
            except TimeoutError:
                pass
        policy = self.output_policy(policy)
        return ProcessReadOutputResult(
            process=await self.inspect(handle),
            stdout=record.stdout.read(stdout_start_offset or 0, policy),
            stderr=record.stderr.read(stderr_start_offset or 0, policy),
        )

    async def write_stdin(
        self, handle: BoundProcessHandle, data: bytes, *, close_after_write: bool = False
    ) -> ProcessWriteStdinResult:
        token = self.resolve(handle)
        record = self.records.get(token)
        if record is None or not record.stdin_open:
            raise EnvironmentError("Docker stdin is closed or detached", code="environment_not_found")
        if len(data) > 1048576:
            raise EnvironmentError("Stdin request is too large", code="environment_too_large")
        async with record.write_lock:
            raw = getattr(record.connection, "_sock", record.connection)
            with engine_errors(mutation=True):
                await asyncio.to_thread(raw.sendall, data)
            if close_after_write:
                await self.close_stdin(handle)
        return ProcessWriteStdinResult(
            accepted_bytes=len(data), stdin_open=record.stdin_open, receipt=self.commands.receipt()
        )

    async def close_stdin(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt:
        token = self.resolve(handle)
        record = self.records.get(token)
        if record is None:
            raise EnvironmentError("Docker stdin is detached", code="environment_not_found")
        if record.stdin_open:
            raw = getattr(record.connection, "_sock", record.connection)
            with engine_errors(mutation=True):
                await asyncio.to_thread(raw.shutdown, socket.SHUT_WR)
            record.stdin_open = False
        return self.commands.receipt()

    async def _signal(self, handle: BoundProcessHandle, number: int) -> ProcessInfo:
        token = self.resolve(handle)
        info = await self.inspect(handle)
        if info.status.phase != "running":
            return info
        with engine_errors():
            native = await asyncio.to_thread(self.commands.engine.client.api.exec_inspect, token)
        try:
            tag = json.loads(native["ProcessConfig"]["arguments"][-1])["tag"]
        except (KeyError, IndexError, TypeError, ValueError):
            tag = None
        if not isinstance(tag, str) or not re.fullmatch(r"[0-9a-f]{32}", tag):
            raise EnvironmentError("Exec was not created by this Provider", code="environment_unsupported")
        await self.commands.execute(
            [
                self.commands.config.python,
                "-I",
                "-c",
                "import json,os,sys; pid,start=json.load(open('/tmp/a13n/'+sys.argv[1])); "
                "current=open('/proc/'+str(pid)+'/stat').read().rsplit(')',1)[1].split()[19]; "
                "assert current==start, 'Process identity changed'; os.killpg(pid,int(sys.argv[2]))",
                tag,
                str(number),
            ]
        )
        return await self.inspect(handle)

    async def signal(
        self, handle: BoundProcessHandle, signal: Literal["interrupt", "terminate"]
    ) -> ProcessSignalResult:
        info = await self._signal(handle, 2 if signal == "interrupt" else 15)
        return ProcessSignalResult(accepted=True, process=info, receipt=self.commands.receipt())

    async def kill(self, handle: BoundProcessHandle) -> ProcessControlResult:
        return ProcessControlResult(process=await self._signal(handle, 9), receipt=self.commands.receipt())

    async def wait(
        self,
        handle: BoundProcessHandle,
        *,
        condition: Literal["initial_terminal", "tree_cleaned"],
        timeout_seconds: float,
    ) -> ProcessInfo:
        if condition != "initial_terminal":
            raise EnvironmentError("Docker cannot verify command tree cleanup", code="environment_unsupported")
        if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
            raise EnvironmentError("Invalid process wait", code="environment_request_invalid")
        info = await self.inspect(handle)
        try:
            async with asyncio.timeout(timeout_seconds):
                while info.status.phase in {"starting", "running"}:
                    await asyncio.sleep(0.05)
                    info = await self.inspect(handle)
        except TimeoutError:
            pass
        return info

    async def release(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt:
        token = self.resolve(handle)
        record = self.records.pop(token, None)
        if record is not None:
            try:
                await record.close()
            finally:
                record.stdout.release()
                record.stderr.release()
        return self.commands.receipt()

    async def close(self) -> None:
        outcomes = await asyncio.gather(
            *(self.release(self.handle(token)) for token in tuple(self.records)), return_exceptions=True
        )
        errors = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
        if errors:
            raise ExceptionGroup("Docker observation cleanup failed", errors)

    def output_policy(self, policy: EnvironmentOutputPolicy) -> EnvironmentOutputPolicy:
        return policy.model_copy(
            update={"max_inline_bytes": min(policy.max_inline_bytes, self.commands.config.max_output_preview_bytes)}
        )

    async def exec(self, request: CommandRequest) -> ShellExecResult:
        started = await self.start(request)
        handle = started.process.handle
        try:
            info = await self.inspect(handle)
            while info.status.phase in {"starting", "running"}:
                info = await self.wait(handle, condition="initial_terminal", timeout_seconds=1)
            record = self.records[self.resolve(handle)]
            # Descendants may inherit stdout after the initial process exits.
            # Capture currently available output without waiting for that tree.
            if record.task is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(record.task), 0.1)
                except TimeoutError:
                    await record.close()
            return ShellExecResult(
                status=info.status,
                receipt=started.receipt,
                output=ProcessOutputSnapshot(
                    stdout=await record.stdout.materialize(self.output_policy(request.output_policy), self.retention),
                    stderr=await record.stderr.materialize(self.output_policy(request.output_policy), self.retention),
                ),
            )
        except asyncio.CancelledError:
            await self.kill(handle)
            raise
        finally:
            await self.release(handle)
