"""Fresh Worker command facets; opaque tokens remain bound to one relay use."""

from __future__ import annotations

from typing import Literal

from a13n_harness.providers.environment.commands import (
    BoundProcessHandle,
    CommandRequest,
    PortObservation,
    PortTarget,
    ProcessControlResult,
    ProcessDiscovery,
    ProcessIdentity,
    ProcessInfo,
    ProcessReadOutputResult,
    ProcessSignalResult,
    ProcessStartResult,
    ProcessWriteStdinResult,
    ShellExecResult,
)
from a13n_harness.providers.environment.models import EnvironmentOperationReceipt
from a13n_harness.providers.environment.retention import (
    BoundOutputCursor,
    BoundOutputReference,
    EnvironmentOutputPolicy,
    EnvironmentOutputReadResult,
)
from pydantic import BaseModel, JsonValue, TypeAdapter

from . import relay_commands as wire
from .relay_client import RelayUseClient
from .relay_values import (
    BinaryValue,
    CommandInput,
    OutputCursor,
    OutputPage,
    OutputReference,
    ProcessHandle,
    ProcessObservation,
    ProcessOutput,
    ProcessReceipt,
    ShellResult,
    SignalReceipt,
)

_PAYLOAD = TypeAdapter(dict[str, JsonValue])


async def _call[T: BaseModel](
    client: RelayUseClient, request: wire.CommandOperation, result_type: type[T], *, timeout_seconds: float = 30
) -> T:
    result = await client.call(
        request.operation,
        _PAYLOAD.validate_python(request.model_dump(mode="json", exclude={"operation"})),
        timeout_seconds=timeout_seconds,
    )
    return result_type.model_validate(result)


class RelayShellOperations:
    def __init__(self, client: RelayUseClient) -> None:
        self._client = client

    async def exec(self, request: CommandRequest) -> ShellExecResult:
        result = await _call(self._client, wire.ShellExec(request=CommandInput.from_value(request)), ShellResult)
        return result.to_value()


class RelayProcessOperations:
    def __init__(self, client: RelayUseClient) -> None:
        self._client = client

    async def list(self, *, limit: int) -> ProcessDiscovery:
        result = await _call(self._client, wire.ProcessList(limit=limit), wire.ProcessDiscovery)
        return ProcessDiscovery(
            processes=tuple(process.to_value() for process in result.processes), has_more=result.has_more
        )

    async def start(self, request: CommandRequest) -> ProcessStartResult:
        result = await _call(self._client, wire.ProcessStart(request=CommandInput.from_value(request)), ProcessReceipt)
        return result.to_start()

    async def rebind(self, identity: ProcessIdentity, *, output_policy: EnvironmentOutputPolicy) -> ProcessInfo:
        result = await _call(
            self._client, wire.ProcessRebind(identity=identity, output_policy=output_policy), ProcessObservation
        )
        return result.to_value()

    async def inspect(self, handle: BoundProcessHandle) -> ProcessInfo:
        result = await _call(
            self._client, wire.ProcessInspect(handle=ProcessHandle.from_value(handle)), ProcessObservation
        )
        return result.to_value()

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
        result = await _call(
            self._client,
            wire.ProcessReadOutput(
                handle=ProcessHandle.from_value(handle),
                stdout_cursor=None if stdout_cursor is None else OutputCursor.from_value(stdout_cursor),
                stderr_cursor=None if stderr_cursor is None else OutputCursor.from_value(stderr_cursor),
                stdout_start_offset=stdout_start_offset,
                stderr_start_offset=stderr_start_offset,
                wait_seconds=wait_seconds,
                policy=policy,
            ),
            ProcessOutput,
            timeout_seconds=min(60, max(30, wait_seconds * 2 + 1)),
        )
        return result.to_value()

    async def write_stdin(
        self, handle: BoundProcessHandle, data: bytes, *, close_after_write: bool = False
    ) -> ProcessWriteStdinResult:
        return await _call(
            self._client,
            wire.ProcessWriteStdin(
                handle=ProcessHandle.from_value(handle),
                data=BinaryValue.from_value(data),
                close_after_write=close_after_write,
            ),
            ProcessWriteStdinResult,
        )

    async def close_stdin(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt:
        return await _call(
            self._client, wire.ProcessCloseStdin(handle=ProcessHandle.from_value(handle)), EnvironmentOperationReceipt
        )

    async def signal(
        self, handle: BoundProcessHandle, signal: Literal["interrupt", "terminate"]
    ) -> ProcessSignalResult:
        result = await _call(
            self._client, wire.ProcessSignal(handle=ProcessHandle.from_value(handle), signal=signal), SignalReceipt
        )
        return result.to_value()

    async def wait(
        self,
        handle: BoundProcessHandle,
        *,
        condition: Literal["initial_terminal", "tree_cleaned"],
        timeout_seconds: float,
    ) -> ProcessInfo:
        result = await _call(
            self._client,
            wire.ProcessWait(
                handle=ProcessHandle.from_value(handle), condition=condition, timeout_seconds=timeout_seconds
            ),
            ProcessObservation,
            timeout_seconds=min(60, timeout_seconds + 1),
        )
        return result.to_value()

    async def kill(self, handle: BoundProcessHandle) -> ProcessControlResult:
        result = await _call(self._client, wire.ProcessKill(handle=ProcessHandle.from_value(handle)), ProcessReceipt)
        return result.to_control()

    async def release(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt:
        return await _call(
            self._client, wire.ProcessRelease(handle=ProcessHandle.from_value(handle)), EnvironmentOperationReceipt
        )


class RelayOutputOperations:
    def __init__(self, client: RelayUseClient) -> None:
        self._client = client

    async def read(
        self,
        reference: BoundOutputReference,
        *,
        cursor: BoundOutputCursor | None = None,
        start_offset: int | None = None,
        policy: EnvironmentOutputPolicy,
    ) -> EnvironmentOutputReadResult:
        result = await _call(
            self._client,
            wire.OutputRead(
                reference=OutputReference.from_value(reference),
                cursor=None if cursor is None else OutputCursor.from_value(cursor),
                start_offset=start_offset,
                policy=policy,
            ),
            OutputPage,
        )
        return result.to_output_value()

    async def release(
        self, *, reference: BoundOutputReference | None = None, cursor: BoundOutputCursor | None = None
    ) -> EnvironmentOperationReceipt:
        return await _call(
            self._client,
            wire.OutputRelease(
                reference=None if reference is None else OutputReference.from_value(reference),
                cursor=None if cursor is None else OutputCursor.from_value(cursor),
            ),
            EnvironmentOperationReceipt,
        )


class RelayPortOperations:
    def __init__(self, client: RelayUseClient) -> None:
        self._client = client

    async def inspect(self, target: PortTarget) -> PortObservation:
        return await _call(self._client, wire.PortInspect(target=target), PortObservation)

    async def wait(
        self, target: PortTarget, *, desired: Literal["listening", "not_listening"], timeout_seconds: float
    ) -> PortObservation:
        return await _call(
            self._client,
            wire.PortWait(target=target, desired=desired, timeout_seconds=timeout_seconds),
            PortObservation,
            timeout_seconds=min(60, timeout_seconds + 1),
        )
