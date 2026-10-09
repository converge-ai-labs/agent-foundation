"""Operation dispatch against one captured mount publication."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from a13n_environment.commands import (
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
from a13n_environment.computer import (
    COMPUTER_INPUT_ACTIONS,
    ComputerActionResult,
    ComputerClick,
    ComputerDescription,
    ComputerDrag,
    ComputerInput,
    ComputerMove,
    ComputerScreenshot,
    ComputerScroll,
)
from a13n_environment.models import (
    EnvironmentAction,
    EnvironmentError,
    EnvironmentOperationReceipt,
)
from a13n_environment.retention import (
    BoundOutputCursor,
    BoundOutputReference,
    EnvironmentOutputPolicy,
    EnvironmentOutputReadResult,
)

from ._mount import (
    _EnteredMount,
    _validate_process_result_identity,
    _validate_provider_artifacts,
)

if TYPE_CHECKING:
    from .coordinator import CompositeBoundEnvironment


class _ComputerFacade:
    def __init__(self, environment: CompositeBoundEnvironment) -> None:
        self._environment = environment

    async def describe(self, *, alias: str | None = None) -> ComputerDescription:
        entered = self._environment._select_entered(alias)
        async with self._environment._operation_lease(
            entered, EnvironmentAction.COMPUTER_DESCRIBE, "computer"
        ) as entered:
            computer = entered.operations.computer
            if computer is None:
                raise EnvironmentError("Computer operations unavailable.", code="environment_unsupported")
            return await computer.describe()

    async def observe(
        self, *, alias: str | None = None, target_id: str | None = None, max_dimension: int = 1280
    ) -> ComputerScreenshot:
        entered = self._environment._select_entered(alias)
        async with self._environment._operation_lease(
            entered, EnvironmentAction.COMPUTER_OBSERVE, "computer"
        ) as entered:
            computer = entered.operations.computer
            if computer is None:
                raise EnvironmentError("Computer operations unavailable.", code="environment_unsupported")
            result = await computer.observe(target_id=target_id, max_dimension=max_dimension)
            _validate_provider_artifacts(entered, result.observation)
            return result

    async def execute(self, request: ComputerInput, *, alias: str | None = None) -> ComputerActionResult:
        action = COMPUTER_INPUT_ACTIONS[request.kind]
        if isinstance(request, ComputerClick | ComputerMove | ComputerDrag | ComputerScroll):
            observation = request.observation
            entered = self._environment.require_action(
                self._environment._entered_for_execution(observation.execution_id).mount_id, action
            )
            if (
                alias is not None
                and self._environment._select_entered(alias).provider.execution_id != observation.execution_id
            ):
                raise EnvironmentError("Observation belongs to another mount.", code="environment_stale_mount")
            if observation.observed_generation != entered.public.descriptor.generation:
                raise EnvironmentError("Computer observation is stale.", code="environment_stale_mount")
        else:
            entered = self._environment._select_entered(alias)
        async with self._environment._operation_lease(entered, action, "computer") as entered:
            computer = entered.operations.computer
            if computer is None:
                raise EnvironmentError("Computer operations unavailable.", code="environment_unsupported")
            if isinstance(request, ComputerScroll) and request.unit == "steps":
                if max(abs(request.delta_x), abs(request.delta_y)) > 100:
                    raise EnvironmentError("Scroll delta exceeds 100 steps", code="environment_request_invalid")
            result = await computer.execute(request)
            _validate_provider_artifacts(entered, result.receipt)
            return result


class _OutputFacade:
    def __init__(self, environment: CompositeBoundEnvironment) -> None:
        self._environment = environment

    async def read(
        self,
        reference: BoundOutputReference,
        **kwargs: Any,
    ) -> EnvironmentOutputReadResult:
        async with self._prepare(reference, EnvironmentAction.OUTPUT_READ) as entered:
            outputs = entered.operations.outputs
            if outputs is None:
                raise EnvironmentError("Output operation facet is unavailable.", code="environment_unsupported")
            result = await outputs.read(reference, **kwargs)
            _validate_provider_artifacts(entered, result)
            return result

    async def release(
        self,
        *,
        reference: BoundOutputReference | None = None,
        cursor: BoundOutputCursor | None = None,
    ) -> EnvironmentOperationReceipt:
        selected = reference if reference is not None else cursor
        if selected is None:
            raise EnvironmentError("Output release requires a selector.", code="environment_request_invalid")
        async with self._prepare(selected, EnvironmentAction.OUTPUT_RELEASE) as entered:
            outputs = entered.operations.outputs
            if outputs is None:
                raise EnvironmentError("Output operation facet is unavailable.", code="environment_unsupported")
            result = await outputs.release(reference=reference, cursor=cursor)
            _validate_provider_artifacts(entered, result)
            return result

    @asynccontextmanager
    async def _prepare(
        self,
        selected: BoundOutputReference | BoundOutputCursor,
        action: EnvironmentAction,
    ) -> AsyncGenerator[_EnteredMount]:
        entered = self._environment.require_action(
            self._environment._entered_for_execution(selected.execution_id).mount_id, action
        )
        if selected.observed_generation != entered.public.descriptor.generation:
            raise EnvironmentError("Output selector is stale.", code="environment_stale_mount")
        async with self._environment._operation_lease(
            entered,
            action,
            "outputs",
            allow_retired=True,
        ) as entered:
            if entered.operations.outputs is None:
                raise EnvironmentError("Output operation facet is unavailable.", code="environment_unsupported")
            yield entered


class _ShellFacade:
    def __init__(self, environment: CompositeBoundEnvironment) -> None:
        self._environment = environment

    async def exec(self, request: CommandRequest, *, alias: str | None = None) -> ShellExecResult:
        return await self.exec_captured(request, alias=alias)

    async def exec_captured(
        self,
        request: CommandRequest,
        *,
        alias: str | None = None,
        expected_mount_id: str | None = None,
    ) -> ShellExecResult:
        """Dispatch bounded foreground execution under one exact mount-incarnation lease."""
        entered, provider_request = self._environment._prepare_command(request, alias=alias)
        if expected_mount_id is not None and entered.mount_id != expected_mount_id:
            raise EnvironmentError("Shell mount changed before dispatch.", code="environment_stale_mount")
        async with self._environment._operation_lease(
            entered,
            EnvironmentAction.SHELL_EXEC,
            "shell",
            timeout_seconds=provider_request.limits.wall_time_seconds,
        ) as entered:
            shell = entered.operations.shell
            if shell is None:
                raise EnvironmentError("Shell operation facet is unavailable.", code="environment_unsupported")
            result = await shell.exec(provider_request)
            _validate_provider_artifacts(entered, result)
            if any(capture.reference is not None for capture in (result.output.stdout, result.output.stderr)):
                raise EnvironmentError(
                    "Captured shell execution returned retained resources.", code="environment_provider_failure"
                )
            return result


class _ProcessFacade:
    def __init__(self, environment: CompositeBoundEnvironment) -> None:
        self._environment = environment

    async def list(self, *, alias: str | None = None, limit: int = 50) -> ProcessDiscovery:
        entered = self._environment._select_entered(alias)
        async with self._environment._operation_lease(
            entered,
            EnvironmentAction.PROCESS_LIST,
            "processes",
        ) as entered:
            processes = entered.operations.processes
            if processes is None:
                raise EnvironmentError("Process operation facet is unavailable.", code="environment_unsupported")
            result = await processes.list(limit=limit)
            _validate_provider_artifacts(entered, result)
            if len(result.processes) > limit:
                raise EnvironmentError("Process listing exceeded its limit.", code="environment_provider_failure")
            # Discovery references do not pin a mount or attach output streams.
            return result

    async def start(
        self,
        request: CommandRequest,
        *,
        alias: str | None = None,
        required_actions: frozenset[EnvironmentAction] = frozenset({EnvironmentAction.PROCESS_START}),
        expected_mount_id: str | None = None,
    ) -> ProcessStartResult:
        entered, provider_request = self._environment._prepare_command(request, alias=alias)
        if expected_mount_id is not None and entered.mount_id != expected_mount_id:
            raise EnvironmentError("Process mount changed before dispatch.", code="environment_stale_mount")
        for action in required_actions:
            selected = self._environment.require_action(entered.mount_id, action)
            if selected is not entered:
                raise EnvironmentError("Process mount changed before dispatch.", code="environment_stale_mount")
        async with self._environment._operation_lease(
            entered,
            EnvironmentAction.PROCESS_START,
            "processes",
            timeout_seconds=request.limits.wall_time_seconds,
        ) as entered:
            # Readiness may republish a narrower descriptor. Validate the whole
            # compound background contract before creating a native process.
            for action in required_actions:
                self._environment.require_action(entered.mount_id, action)
            processes = entered.operations.processes
            if processes is None:
                raise EnvironmentError("Process operation facet is unavailable.", code="environment_unsupported")
            result = await processes.start(provider_request)
            if not isinstance(result, ProcessStartResult):
                raise EnvironmentError(
                    "Process provider returned an invalid start result.",
                    code="environment_provider_failure",
                )
            _validate_provider_artifacts(entered, result)
            self._environment._track_process_handle(result.process.handle, added=True)
            return result

    async def rebind(
        self,
        identity: ProcessIdentity,
        *,
        output_policy: EnvironmentOutputPolicy,
    ) -> ProcessInfo:
        entered = self._environment._entered_for_process_identity(identity)
        async with self._environment._operation_lease(
            entered,
            EnvironmentAction.PROCESS_INSPECT,
            "processes",
        ) as entered:
            processes = entered.operations.processes
            if processes is None:
                raise EnvironmentError("Process operation facet is unavailable.", code="environment_unsupported")
            result = await processes.rebind(identity, output_policy=output_policy)
            _validate_provider_artifacts(entered, result)
            if result.handle.identity != identity:
                raise EnvironmentError(
                    "Environment provider retargeted a restored process identity.",
                    code="environment_provider_failure",
                )
            self._environment._track_process_handle(result.handle, added=True)
            return result

    async def inspect(self, handle: BoundProcessHandle) -> ProcessInfo:
        return await self._call(handle, EnvironmentAction.PROCESS_INSPECT, "inspect")

    async def read_output(self, handle: BoundProcessHandle, **kwargs: Any) -> ProcessReadOutputResult:
        return await self._call(handle, EnvironmentAction.PROCESS_READ_OUTPUT, "read_output", **kwargs)

    async def write_stdin(self, handle: BoundProcessHandle, data: bytes, **kwargs: Any) -> ProcessWriteStdinResult:
        if kwargs.get("close_after_write") is True:
            self._environment.require_action(
                self._environment._entered_for_handle(handle).mount_id, EnvironmentAction.PROCESS_CLOSE_STDIN
            )
        return await self._call(handle, EnvironmentAction.PROCESS_WRITE_STDIN, "write_stdin", data, **kwargs)

    async def close_stdin(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt:
        return await self._call(handle, EnvironmentAction.PROCESS_CLOSE_STDIN, "close_stdin")

    async def signal(self, handle: BoundProcessHandle, signal: str) -> ProcessSignalResult:
        return await self._call(handle, EnvironmentAction.PROCESS_SIGNAL, "signal", signal)

    async def wait(self, handle: BoundProcessHandle, **kwargs: Any) -> ProcessInfo:
        timeout = kwargs.get("timeout_seconds")
        return await self._call(
            handle,
            EnvironmentAction.PROCESS_WAIT,
            "wait",
            timeout_seconds=timeout if isinstance(timeout, int | float) else None,
            semantic_kwargs=kwargs,
        )

    async def kill(self, handle: BoundProcessHandle) -> ProcessControlResult:
        return await self._call(handle, EnvironmentAction.PROCESS_KILL, "kill")

    async def release(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt:
        result = await self._call(handle, EnvironmentAction.PROCESS_RELEASE, "release")
        self._environment._track_process_handle(handle, added=False)
        return result

    async def _call(
        self,
        handle: BoundProcessHandle,
        action: EnvironmentAction,
        method: str,
        *args: Any,
        timeout_seconds: float | None = None,
        semantic_kwargs: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        entered = self._environment._entered_for_handle(handle)
        async with self._environment._operation_lease(
            entered,
            action,
            "processes",
            timeout_seconds=timeout_seconds,
            allow_retired=True,
        ) as entered:
            processes = entered.operations.processes
            if processes is None:
                raise EnvironmentError("Process operation facet is unavailable.", code="environment_unsupported")
            call = getattr(processes, method)
            try:
                result = await call(handle, *args, **dict(semantic_kwargs or kwargs))
            except EnvironmentError as exc:
                if exc.code == "environment_not_found":
                    self._environment._track_process_handle(handle, added=False)
                raise
            _validate_provider_artifacts(entered, result)
            _validate_process_result_identity(handle, result)
            return result


class _PortFacade:
    def __init__(self, environment: CompositeBoundEnvironment) -> None:
        self._environment = environment

    async def inspect(self, target: PortTarget, *, alias: str | None = None) -> PortObservation:
        return await self._call(target, EnvironmentAction.PORT_INSPECT, "inspect", alias=alias)

    async def wait(self, target: PortTarget, *, alias: str | None = None, **kwargs: Any) -> PortObservation:
        timeout = kwargs.get("timeout_seconds")
        return await self._call(
            target,
            EnvironmentAction.PORT_WAIT,
            "wait",
            alias=alias,
            timeout_seconds=timeout if isinstance(timeout, int | float) else None,
            kwargs=kwargs,
        )

    async def _call(
        self,
        target: PortTarget,
        action: EnvironmentAction,
        method: str,
        *,
        alias: str | None,
        timeout_seconds: float | None = None,
        kwargs: Mapping[str, Any] | None = None,
    ) -> Any:
        entered = self._environment._select_entered(alias)
        async with self._environment._operation_lease(
            entered,
            action,
            "ports",
            timeout_seconds=timeout_seconds,
        ) as entered:
            ports = entered.operations.ports
            if ports is None:
                raise EnvironmentError("Port operation facet is unavailable.", code="environment_unsupported")
            call = getattr(ports, method)
            result = await call(target, **dict(kwargs or {}))
            _validate_provider_artifacts(entered, result)
            return result
