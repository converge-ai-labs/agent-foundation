"""Semantic command and retained-output dispatch on the owning Control Session."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Literal, assert_never

from a13n_harness.providers.environment.commands import PortTarget, ProcessIdentity
from a13n_harness.providers.environment.models import EnvironmentAction, EnvironmentError
from a13n_harness.providers.environment.operations import EnvironmentOperations
from a13n_harness.providers.environment.retention import EnvironmentOutputPolicy
from pydantic import BaseModel, Field, JsonValue, TypeAdapter, model_validator

from ..domain import DomainModel
from .relay_protocol import RelayRequest, operation_permissions
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


class ShellExec(DomainModel):
    operation: Literal["shell.exec"] = "shell.exec"
    request: CommandInput


class ProcessStart(DomainModel):
    operation: Literal["process.start"] = "process.start"
    request: CommandInput


class ProcessList(DomainModel):
    operation: Literal["process.list"] = "process.list"
    limit: int = Field(gt=0)


class ProcessRebind(DomainModel):
    operation: Literal["process.rebind"] = "process.rebind"
    identity: ProcessIdentity
    output_policy: EnvironmentOutputPolicy


class ProcessInspect(DomainModel):
    operation: Literal["process.inspect"] = "process.inspect"
    handle: ProcessHandle


class ProcessReadOutput(DomainModel):
    operation: Literal["process.read_output"] = "process.read_output"
    handle: ProcessHandle
    stdout_cursor: OutputCursor | None = None
    stderr_cursor: OutputCursor | None = None
    stdout_start_offset: int | None = Field(default=None, ge=0)
    stderr_start_offset: int | None = Field(default=None, ge=0)
    wait_seconds: float = Field(default=0, ge=0, le=60, allow_inf_nan=False)
    policy: EnvironmentOutputPolicy


class ProcessWriteStdin(DomainModel):
    operation: Literal["process.write_stdin"] = "process.write_stdin"
    handle: ProcessHandle
    data: BinaryValue = Field(repr=False)
    close_after_write: bool = False


class ProcessCloseStdin(DomainModel):
    operation: Literal["process.close_stdin"] = "process.close_stdin"
    handle: ProcessHandle


class ProcessSignal(DomainModel):
    operation: Literal["process.signal"] = "process.signal"
    handle: ProcessHandle
    signal: Literal["interrupt", "terminate"]


class ProcessWait(DomainModel):
    operation: Literal["process.wait"] = "process.wait"
    handle: ProcessHandle
    condition: Literal["initial_terminal", "tree_cleaned"]
    timeout_seconds: float = Field(gt=0, le=60, allow_inf_nan=False)


class ProcessKill(DomainModel):
    operation: Literal["process.kill"] = "process.kill"
    handle: ProcessHandle


class ProcessRelease(DomainModel):
    operation: Literal["process.release"] = "process.release"
    handle: ProcessHandle


class OutputRead(DomainModel):
    operation: Literal["output.read"] = "output.read"
    reference: OutputReference
    cursor: OutputCursor | None = None
    start_offset: int | None = Field(default=None, ge=0)
    policy: EnvironmentOutputPolicy


class OutputRelease(DomainModel):
    operation: Literal["output.release"] = "output.release"
    reference: OutputReference | None = None
    cursor: OutputCursor | None = None

    @model_validator(mode="after")
    def _one_reference(self) -> OutputRelease:
        if (self.reference is None) == (self.cursor is None):
            raise ValueError("Output release requires exactly one reference or cursor")
        return self


class PortInspect(DomainModel):
    operation: Literal["port.inspect"] = "port.inspect"
    target: PortTarget


class PortWait(DomainModel):
    operation: Literal["port.wait"] = "port.wait"
    target: PortTarget
    desired: Literal["listening", "not_listening"]
    timeout_seconds: float = Field(gt=0, le=60, allow_inf_nan=False)


type CommandOperation = Annotated[
    ShellExec
    | ProcessStart
    | ProcessList
    | ProcessRebind
    | ProcessInspect
    | ProcessReadOutput
    | ProcessWriteStdin
    | ProcessCloseStdin
    | ProcessSignal
    | ProcessWait
    | ProcessKill
    | ProcessRelease
    | OutputRead
    | OutputRelease
    | PortInspect
    | PortWait,
    Field(discriminator="operation"),
]
COMMAND_OPERATION = TypeAdapter[CommandOperation](CommandOperation)
_JSON = TypeAdapter(JsonValue)


class ProcessDiscovery(DomainModel):
    processes: tuple[ProcessObservation, ...]
    has_more: bool


class CommandRelayDispatch:
    def __init__(self, operations: EnvironmentOperations, permissions: frozenset[EnvironmentAction]) -> None:
        self._operations = operations
        self._permissions = permissions

    def prepare(self, message: RelayRequest) -> Callable[[], Awaitable[JsonValue]]:
        operation, payload = message.operation, message.payload
        if "operation" in payload:
            raise ValueError("Command relay payload cannot override its operation")
        request = COMMAND_OPERATION.validate_python({"operation": operation, **payload}, extra="forbid")
        if not operation_permissions(operation) <= self._permissions:
            raise EnvironmentError("Operation exceeds the admitted access policy", code="environment_forbidden")
        facets = {
            "shell": self._operations.shell,
            "process": self._operations.processes,
            "output": self._operations.outputs,
            "port": self._operations.ports,
        }
        if facets[operation.split(".", 1)[0]] is None:
            raise EnvironmentError("Operation facet is unavailable", code="environment_unsupported")

        async def execute() -> JsonValue:
            result = await self._execute(request)
            return _JSON.validate_python(result.model_dump(mode="json"))

        return execute

    async def _execute(self, request: CommandOperation) -> BaseModel:
        if isinstance(request, ShellExec):
            shell = self._operations.shell
            assert shell is not None
            return ShellResult.from_value(await shell.exec(request.request.to_value()))
        if isinstance(request, OutputRead | OutputRelease):
            outputs = self._operations.outputs
            assert outputs is not None
            if isinstance(request, OutputRead):
                return OutputPage.from_value(
                    await outputs.read(
                        request.reference.to_value(),
                        cursor=None if request.cursor is None else request.cursor.to_value(),
                        start_offset=request.start_offset,
                        policy=request.policy,
                    )
                )
            return await outputs.release(
                reference=None if request.reference is None else request.reference.to_value(),
                cursor=None if request.cursor is None else request.cursor.to_value(),
            )
        if isinstance(request, PortInspect | PortWait):
            ports = self._operations.ports
            assert ports is not None
            if isinstance(request, PortInspect):
                return await ports.inspect(request.target)
            return await ports.wait(request.target, desired=request.desired, timeout_seconds=request.timeout_seconds)

        processes = self._operations.processes
        assert processes is not None
        match request:
            case ProcessStart():
                return ProcessReceipt.from_value(await processes.start(request.request.to_value()))
            case ProcessList():
                result = await processes.list(limit=request.limit)
                return ProcessDiscovery(
                    processes=tuple(ProcessObservation.from_value(process) for process in result.processes),
                    has_more=result.has_more,
                )
            case ProcessRebind():
                return ProcessObservation.from_value(
                    await processes.rebind(request.identity, output_policy=request.output_policy)
                )
            case ProcessInspect():
                return ProcessObservation.from_value(await processes.inspect(request.handle.to_value()))
            case ProcessReadOutput():
                return ProcessOutput.from_value(
                    await processes.read_output(
                        request.handle.to_value(),
                        stdout_cursor=None if request.stdout_cursor is None else request.stdout_cursor.to_value(),
                        stderr_cursor=None if request.stderr_cursor is None else request.stderr_cursor.to_value(),
                        stdout_start_offset=request.stdout_start_offset,
                        stderr_start_offset=request.stderr_start_offset,
                        wait_seconds=request.wait_seconds,
                        policy=request.policy,
                    )
                )
            case ProcessWriteStdin():
                return await processes.write_stdin(
                    request.handle.to_value(), request.data.to_value(), close_after_write=request.close_after_write
                )
            case ProcessCloseStdin():
                return await processes.close_stdin(request.handle.to_value())
            case ProcessSignal():
                return SignalReceipt.from_value(await processes.signal(request.handle.to_value(), request.signal))
            case ProcessWait():
                return ProcessObservation.from_value(
                    await processes.wait(
                        request.handle.to_value(), condition=request.condition, timeout_seconds=request.timeout_seconds
                    )
                )
            case ProcessKill():
                return ProcessReceipt.from_value(await processes.kill(request.handle.to_value()))
            case ProcessRelease():
                return await processes.release(request.handle.to_value())
            case _:
                assert_never(request)
