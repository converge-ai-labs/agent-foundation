from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Literal

from a13n_envd_client import EIPSession
from a13n_envd_client.eip import v1 as eip
from a13n_envd_client.errors import EIPMethodError

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
    EnvironmentOutputPolicy,
    EnvironmentOutputSegment,
    OpaqueProcessHandle,
    _unwrap_opaque,
    materialize_capture,
)
from ._common import (
    convert_receipt,
    encode_bytes,
    invoke,
    new_context,
    parse_timestamp,
    raise_converted,
    seconds_to_milliseconds,
    session_client,
)
from .files import EIPFileOperator
from .output import EIPOutputRegistry


@dataclass(slots=True)
class _ProcessRecord:
    handle: eip.ProcessHandle
    output_policy: EnvironmentOutputPolicy
    pending_output_references: list[eip.OutputReference]
    remote_released: bool = False
    release_receipt: EnvironmentOperationReceipt | None = None
    cleanup_owned: bool = False
    kill_completed: bool = False
    release_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def convert_command_request(request: CommandRequest, *, files: EIPFileOperator) -> eip.CommandRequest:
    if request.network != "configured":
        raise EnvironmentError(
            "Per-command network policy requires an outer Host boundary", code="environment_unsupported"
        )
    command = request.command
    if isinstance(command, ArgvCommand):
        executable = (
            eip.ExecutablePath(kind="path", path=files.to_eip_path(command.executable))
            if command.executable.startswith("/")
            else eip.ExecutableName(kind="name", name=command.executable)
        )
        converted_command: eip.CommandSpec = eip.ArgvCommand(
            kind="argv",
            executable_spec=executable,
            arguments=command.arguments,
        )
    elif isinstance(command, ShellCommand):
        converted_command = eip.ShellCommand(
            kind="shell",
            profile_id=command.profile_id,
            script=command.script,
            login=command.login or False,
        )
    else:  # pragma: no cover - Pydantic's discriminated union is closed
        raise TypeError("unsupported command type")
    limits = request.limits
    return eip.CommandRequest(
        command=converted_command,
        cwd=None if request.cwd is None else files.to_eip_path(request.cwd),
        environment=eip.CommandEnvironment(
            set=dict(request.environment.set),
            unset=request.environment.unset,
        ),
        limits=eip.CommandLimits(
            wall_time_ms=None
            if limits.wall_time_seconds is None
            else seconds_to_milliseconds(limits.wall_time_seconds),
            stdin_bytes=limits.stdin_bytes,
            process_count=limits.process_count,
            memory_bytes=limits.memory_bytes,
            cpu_time_ms=None if limits.cpu_time_seconds is None else seconds_to_milliseconds(limits.cpu_time_seconds),
        ),
        initial_stdin=None if request.initial_stdin is None else encode_bytes(request.initial_stdin),
        keep_stdin_open=request.keep_stdin_open,
    )


class _ProcessConversions:
    def __init__(
        self,
        *,
        session: EIPSession,
        files: EIPFileOperator,
        outputs: EIPOutputRegistry,
        provider_type: str,
        environment_id: str,
        mount_id: str,
        generation: str,
    ) -> None:
        self._session = session
        self._files = files
        self._outputs = outputs
        self._provider_type = provider_type
        self._environment_id = environment_id
        self._mount_id = mount_id
        self._generation = generation
        self._records: dict[str, _ProcessRecord] = {}
        self._raw_tokens: dict[eip.ProcessHandle, str] = {}

    def register(self, process: eip.ProcessInfo, policy: EnvironmentOutputPolicy) -> ProcessInfo:
        self._validate_process_identity(process)
        token = self._ensure_record(process, policy)
        return self.convert_process(process, self._records[token].output_policy)

    def convert_process(self, process: eip.ProcessInfo, policy: EnvironmentOutputPolicy) -> ProcessInfo:
        self._validate_process_identity(process)
        token = self._ensure_record(process, policy)
        # Native retention is internal to process observation, not a Harness output permission prerequisite.
        observation_policy = policy.model_copy(update={"overflow": "retain"})
        stdout = self._outputs.capture(process.output.stdout, policy=observation_policy)
        stderr = self._outputs.capture(process.output.stderr, policy=observation_policy)
        info = ProcessInfo(
            handle=BoundProcessHandle(
                mount_id=self._mount_id,
                identity=ProcessIdentity(
                    provider_type=self._provider_type,
                    environment_id=self._environment_id,
                    generation=self._generation,
                    process_id=str(process.handle.root),
                ),
                observed_generation=self._generation,
                handle=OpaqueProcessHandle._from_payload(token),
            ),
            status=self.convert_status(process.status),
            stdin_open=process.stdin_open,
            output=ProcessOutputSnapshot(stdout=stdout, stderr=stderr),
        )
        record = self._records[token]
        for raw, _capture in (
            (process.output.stdout.reference, stdout),
            (process.output.stderr.reference, stderr),
        ):
            if raw not in record.pending_output_references:
                record.pending_output_references.append(raw)
        return info

    def _validate_process_identity(self, process: eip.ProcessInfo) -> None:
        if (
            process.device_id != self._session.descriptor.device_id
            or process.session_id != self._session.descriptor.session_id
            or process.generation != self._session.descriptor.generation
        ):
            raise EnvironmentError("EIP process identity is stale", code="environment_stale_mount")

    def _ensure_record(self, process: eip.ProcessInfo, policy: EnvironmentOutputPolicy) -> str:
        token = self._raw_tokens.get(process.handle)
        if token is None:
            token = f"process-{secrets.token_urlsafe(12)}"
            self._raw_tokens[process.handle] = token
            self._records[token] = _ProcessRecord(
                process.handle,
                policy.model_copy(deep=True),
                [],
            )
        return token

    def resolve(self, handle: BoundProcessHandle) -> tuple[str, _ProcessRecord]:
        if handle.mount_id != self._mount_id or handle.observed_generation != self._generation:
            raise EnvironmentError("Process handle is foreign or stale", code="environment_stale_mount")
        token = _unwrap_opaque(handle.handle, OpaqueProcessHandle)
        record = self._records.get(token)
        if record is None:
            raise EnvironmentError("Process handle is unavailable", code="environment_not_found")
        return token, record

    def receipt(self, receipt: eip.OperationReceipt) -> EnvironmentOperationReceipt:
        return convert_receipt(
            receipt,
            session=self._session,
            mount_id=self._mount_id,
            generation=self._generation,
        )

    async def adopt_command_failure(
        self,
        error: EIPMethodError,
        policy: EnvironmentOutputPolicy,
    ) -> None:
        data = error.error.data
        if data.receipt is not None:
            self.receipt(data.receipt)
        if data.process is not None:
            self._validate_process_identity(data.process)
            token = self._ensure_record(data.process, policy)
            record = self._records[token]
            record.cleanup_owned = True
            record.pending_output_references = [
                data.process.output.stdout.reference,
                data.process.output.stderr.reference,
            ]
            try:
                await asyncio.shield(self.release_record(token, record, kill_first=True))
            except EnvironmentError:
                pass
            return
        if data.output is not None:
            for output in (data.output.stdout, data.output.stderr):
                self._outputs.defer_cleanup(output.reference)
            try:
                await asyncio.shield(self._outputs.cleanup_pending())
            except EnvironmentError:
                pass

    async def cleanup_unreturned_process(
        self,
        process: eip.ProcessInfo,
        policy: EnvironmentOutputPolicy,
    ) -> None:
        self._validate_process_identity(process)
        token = self._ensure_record(process, policy)
        record = self._records[token]
        record.cleanup_owned = True
        record.pending_output_references = [
            process.output.stdout.reference,
            process.output.stderr.reference,
        ]
        try:
            await asyncio.shield(self.release_record(token, record, kill_first=True))
        except EnvironmentError:
            pass

    async def release_record(
        self,
        token: str,
        record: _ProcessRecord,
        *,
        kill_first: bool = False,
    ) -> EnvironmentOperationReceipt:
        async with record.release_lock:
            record.cleanup_owned = True
            if kill_first and not record.kill_completed and not record.remote_released:
                killed = await invoke(
                    session_client(self._session).process_kill(
                        eip.ProcessKillParams(context=new_context(), handle=record.handle)
                    )
                )
                self._validate_process_identity(killed.process)
                self.receipt(killed.receipt)
                record.kill_completed = True
            if not record.remote_released:
                result = await invoke(
                    session_client(self._session).process_release(
                        eip.ProcessReleaseParams(context=new_context(), handle=record.handle)
                    )
                )
                receipt = self.receipt(result.receipt)
                if not result.released:
                    return receipt
                record.remote_released = True
                record.release_receipt = receipt
            while record.pending_output_references:
                reference = record.pending_output_references[0]
                await self._outputs.release_raw(reference)
                record.pending_output_references.pop(0)
            receipt = record.release_receipt
            assert receipt is not None
            self._records.pop(token, None)
            self._raw_tokens.pop(record.handle, None)
            return receipt

    async def cleanup_owned(self) -> None:
        first_error: EnvironmentError | None = None
        for token, record in tuple(self._records.items()):
            if not record.cleanup_owned:
                continue
            try:
                await self.release_record(token, record, kill_first=True)
            except EnvironmentError as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    @staticmethod
    def convert_status(status: eip.ProcessStatus) -> ProcessStatus:
        return ProcessStatus(
            phase=status.phase.value,
            termination_reason=None if status.termination_reason is None else status.termination_reason.value,
            exit_code=status.exit_code,
            signal=None if status.signal is None else status.signal.value,
            started_at=parse_timestamp(status.started_at),
            ended_at=parse_timestamp(status.ended_at),
            cleanup=status.cleanup.value,
        )


class EIPShellOperations:
    def __init__(self, conversions: _ProcessConversions) -> None:
        self._conversions = conversions

    async def exec(self, request: CommandRequest) -> ShellExecResult:
        try:
            result = await session_client(self._conversions._session).shell_exec(
                eip.ShellExecParams(
                    context=new_context(),
                    request=convert_command_request(request, files=self._conversions._files),
                )
            )
        except EIPMethodError as error:
            await self._conversions.adopt_command_failure(error, request.output_policy)
            raise_converted(error)
        except BaseException as error:
            raise_converted(error)
        receipt = self._conversions.receipt(result.receipt)
        raw_outputs = (result.output.stdout, result.output.stderr)
        try:
            stdout = self._conversions._outputs.capture(
                raw_outputs[0],
                policy=request.output_policy.model_copy(update={"overflow": "retain"}),
            )
            stderr = self._conversions._outputs.capture(
                raw_outputs[1],
                policy=request.output_policy.model_copy(update={"overflow": "retain"}),
            )
        except BaseException:
            for output in raw_outputs:
                self._conversions._outputs.defer_cleanup(output.reference)
            try:
                await self._conversions._outputs.cleanup_pending()
            except EnvironmentError:
                pass
            raise
        stdout, stderr = await asyncio.gather(
            materialize_capture(self._conversions._outputs, stdout, request.output_policy),
            materialize_capture(self._conversions._outputs, stderr, request.output_policy),
        )
        for output in raw_outputs:
            self._conversions._outputs.defer_cleanup(output.reference)
        await self._conversions._outputs.cleanup_pending()
        return ShellExecResult(
            status=self._conversions.convert_status(result.status),
            output=ProcessOutputSnapshot(stdout=stdout, stderr=stderr),
            receipt=receipt,
        )


class EIPProcessOperations:
    def __init__(self, conversions: _ProcessConversions) -> None:
        self._conversions = conversions

    async def list(self, *, limit: int) -> ProcessDiscovery:
        raise EnvironmentError("EIP discovery is unsupported.", code="environment_unsupported")

    async def start(self, request: CommandRequest) -> ProcessStartResult:
        try:
            result = await session_client(self._conversions._session).process_start(
                eip.ProcessStartParams(
                    context=new_context(),
                    request=convert_command_request(request, files=self._conversions._files),
                )
            )
        except EIPMethodError as error:
            await self._conversions.adopt_command_failure(error, request.output_policy)
            raise_converted(error)
        except BaseException as error:
            raise_converted(error)
        receipt = self._conversions.receipt(result.receipt)
        try:
            process = self._conversions.register(result.process, request.output_policy)
        except BaseException:
            await self._conversions.cleanup_unreturned_process(result.process, request.output_policy)
            raise
        return ProcessStartResult(process=process, receipt=receipt)

    async def rebind(
        self,
        identity: ProcessIdentity,
        *,
        output_policy: EnvironmentOutputPolicy,
    ) -> ProcessInfo:
        if (
            identity.provider_type != self._conversions._provider_type
            or identity.environment_id != self._conversions._environment_id
            or identity.generation != self._conversions._generation
        ):
            raise EnvironmentError("Process identity belongs to another Environment.", code="environment_stale_mount")
        raw_handle = eip.ProcessHandle(root=identity.process_id)
        result = await invoke(
            session_client(self._conversions._session).process_inspect(
                eip.ProcessInspectParams(context=new_context(), handle=raw_handle)
            )
        )
        if result.process.handle != raw_handle:
            raise EnvironmentError("EIP retargeted a process identity.", code="environment_provider_failure")
        return self._conversions.register(result.process, output_policy)

    async def inspect(self, handle: BoundProcessHandle) -> ProcessInfo:
        _, record = self._conversions.resolve(handle)
        result = await invoke(
            session_client(self._conversions._session).process_inspect(
                eip.ProcessInspectParams(context=new_context(), handle=record.handle)
            )
        )
        return self._conversions.convert_process(result.process, record.output_policy)

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
        _, record = self._conversions.resolve(handle)
        result = await invoke(
            session_client(self._conversions._session).process_inspect(
                eip.ProcessInspectParams(context=new_context(), handle=record.handle)
            )
        )
        process = self._conversions.convert_process(result.process, record.output_policy)
        assert process.output is not None
        stdout = await self._read_stream(
            process.output.stdout,
            cursor=stdout_cursor,
            start_offset=stdout_start_offset,
            wait_seconds=wait_seconds,
            policy=policy,
        )
        stderr = await self._read_stream(
            process.output.stderr,
            cursor=stderr_cursor,
            start_offset=stderr_start_offset,
            wait_seconds=wait_seconds,
            policy=policy,
        )
        return ProcessReadOutputResult(process=process, stdout=stdout, stderr=stderr)

    async def _read_stream(
        self,
        capture,
        *,
        cursor: BoundOutputCursor | None,
        start_offset: int | None,
        wait_seconds: float,
        policy: EnvironmentOutputPolicy,
    ) -> ProcessStreamRead:
        if capture.reference is None:
            data = capture.inline or b""
            offset = start_offset or 0
            if offset < capture.available_start or offset > capture.available_end:
                raise EnvironmentError("Output offset is unavailable", code="environment_output_gap")
            allowed = min(policy.max_inline_bytes, policy.max_output_bytes)
            end = min(len(data), offset + allowed)
            chunks = EnvironmentOutputSegment(start_offset=offset, data=data[offset:end]) if end > offset else None
            return ProcessStreamRead(
                chunks=() if chunks is None else (chunks,),
                next_cursor=None,
                capture=capture,
            )
        page = await self._conversions._outputs.read(
            capture.reference,
            cursor=cursor,
            start_offset=start_offset,
            policy=policy,
            wait_seconds=wait_seconds,
        )
        return ProcessStreamRead(
            chunks=page.chunks,
            next_cursor=page.next_cursor,
            capture=page.capture,
        )

    async def write_stdin(
        self,
        handle: BoundProcessHandle,
        data: bytes,
        *,
        close_after_write: bool = False,
    ) -> ProcessWriteStdinResult:
        _, record = self._conversions.resolve(handle)
        result = await invoke(
            session_client(self._conversions._session).process_write_stdin(
                eip.ProcessWriteStdinParams(
                    context=new_context(),
                    handle=record.handle,
                    data=encode_bytes(data),
                    close_after_write=close_after_write,
                )
            )
        )
        return ProcessWriteStdinResult(
            accepted_bytes=result.accepted_bytes,
            stdin_open=result.stdin_open,
            receipt=self._conversions.receipt(result.receipt),
        )

    async def close_stdin(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt:
        _, record = self._conversions.resolve(handle)
        result = await invoke(
            session_client(self._conversions._session).process_close_stdin(
                eip.ProcessCloseStdinParams(context=new_context(), handle=record.handle)
            )
        )
        return self._conversions.receipt(result.receipt)

    async def signal(
        self,
        handle: BoundProcessHandle,
        signal: Literal["interrupt", "terminate"],
    ) -> ProcessSignalResult:
        _, record = self._conversions.resolve(handle)
        result = await invoke(
            session_client(self._conversions._session).process_signal(
                eip.ProcessSignalParams(
                    context=new_context(),
                    handle=record.handle,
                    signal=eip.RequestedProcessSignal(signal),
                )
            )
        )
        receipt = self._conversions.receipt(result.receipt)
        return ProcessSignalResult(
            accepted=result.accepted,
            process=self._conversions.convert_process(result.process, record.output_policy),
            receipt=receipt,
        )

    async def wait(
        self,
        handle: BoundProcessHandle,
        *,
        condition: Literal["initial_terminal", "tree_cleaned"],
        timeout_seconds: float,
    ) -> ProcessInfo:
        _, record = self._conversions.resolve(handle)
        result = await invoke(
            session_client(self._conversions._session).process_wait(
                eip.ProcessWaitParams(
                    context=new_context(timeout_seconds=timeout_seconds),
                    handle=record.handle,
                    condition=eip.ProcessWaitCondition(condition),
                )
            )
        )
        return self._conversions.convert_process(result.process, record.output_policy)

    async def kill(self, handle: BoundProcessHandle) -> ProcessControlResult:
        _, record = self._conversions.resolve(handle)
        result = await invoke(
            session_client(self._conversions._session).process_kill(
                eip.ProcessKillParams(context=new_context(), handle=record.handle)
            )
        )
        receipt = self._conversions.receipt(result.receipt)
        return ProcessControlResult(
            process=self._conversions.convert_process(result.process, record.output_policy),
            receipt=receipt,
        )

    async def release(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt:
        token, record = self._conversions.resolve(handle)
        info = await self.inspect(handle)
        record.cleanup_owned = True
        if info.status.phase in {"starting", "running"}:
            return EnvironmentOperationReceipt(
                mount_id=handle.mount_id,
                observed_generation=handle.observed_generation,
                operation_id="observation-release",
                stage="completed",
                outcome="succeeded",
            )
        return await self._conversions.release_record(token, record)

    async def cleanup_owned(self) -> None:
        await self._conversions.cleanup_owned()


class EIPPortOperations:
    def __init__(self, conversions: _ProcessConversions) -> None:
        self._conversions = conversions

    async def inspect(self, target: PortTarget) -> PortObservation:
        result = await invoke(
            session_client(self._conversions._session).port_inspect(
                eip.PortInspectParams(context=new_context(), target=self._target(target))
            )
        )
        return self._observation(target, result.observation)

    async def wait(
        self,
        target: PortTarget,
        *,
        desired: Literal["listening", "not_listening"],
        timeout_seconds: float,
    ) -> PortObservation:
        result = await invoke(
            session_client(self._conversions._session).port_wait(
                eip.PortWaitParams(
                    context=new_context(timeout_seconds=timeout_seconds),
                    target=self._target(target),
                    desired_status=eip.DesiredPortStatus(desired),
                )
            )
        )
        return self._observation(target, result.observation)

    @staticmethod
    def _target(target: PortTarget) -> eip.PortTarget:
        return eip.PortTarget(
            protocol="tcp",
            address=eip.PortAddress(target.address),
            port=target.port,
        )

    @staticmethod
    def _observation(target: PortTarget, observation: eip.PortObservation) -> PortObservation:
        observed_at = parse_timestamp(observation.observed_at)
        assert observed_at is not None
        return PortObservation(
            target=target,
            status=observation.status.value,
            observed_at=observed_at,
        )
