"""Operation dispatch against one captured mount publication."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from ._mount import (
    _EnteredMount,
    _validate_process_result_identity,
    _validate_provider_artifacts,
)
from .commands import (
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
from .models import (
    EnvironmentAction,
    EnvironmentError,
    EnvironmentOperationReceipt,
)
from .retention import (
    BoundOutputCursor,
    BoundOutputReference,
    EnvironmentOutputPolicy,
    EnvironmentOutputReadResult,
)

if TYPE_CHECKING:
    from .coordinator import CompositeBoundEnvironment


class _UnavailableFacet:
    __slots__ = ("_family",)

    def __init__(self, family: str) -> None:
        self._family = family

    def __getattr__(self, method: str) -> Any:
        async def unavailable(*args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            raise EnvironmentError(
                f"Environment operation family {self._family!r} is unavailable.",
                code="environment_unsupported",
                details={"family": self._family, "method": method},
                retry_hint="dependency_change",
            )

        return unavailable


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
        entered = self._environment.require_action(selected.mount_id, action)
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
        entered, provider_request = self._environment._prepare_command(request, alias=alias)
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
            return result

    async def exec_captured(
        self,
        request: CommandRequest,
        *,
        alias: str | None = None,
        expected_mount_id: str | None = None,
    ) -> ShellExecResult:
        """Preflight and hold one mount incarnation through foreground output materialization."""
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
            self._environment.require_action(handle.mount_id, EnvironmentAction.PROCESS_CLOSE_STDIN)
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

    async def inspect(self, target: PortTarget) -> PortObservation:
        return await self._call(target, EnvironmentAction.PORT_INSPECT, "inspect")

    async def wait(self, target: PortTarget, **kwargs: Any) -> PortObservation:
        timeout = kwargs.get("timeout_seconds")
        return await self._call(
            target,
            EnvironmentAction.PORT_WAIT,
            "wait",
            timeout_seconds=timeout if isinstance(timeout, int | float) else None,
            kwargs=kwargs,
        )

    async def _call(
        self,
        target: PortTarget,
        action: EnvironmentAction,
        method: str,
        *,
        timeout_seconds: float | None = None,
        kwargs: Mapping[str, Any] | None = None,
    ) -> Any:
        entered = self._environment._select_entered(target.alias)
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
            result = await call(target.model_copy(update={"alias": None}), **dict(kwargs or {}))
            _validate_provider_artifacts(entered, result)
            return result
