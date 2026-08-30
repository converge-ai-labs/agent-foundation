"""Aggregate Environment entry, dynamic routing, leases, and lifecycle ownership."""

from __future__ import annotations

import asyncio
import json
import math
import sys
from collections.abc import AsyncGenerator, Coroutine, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, JsonValue

from a13n_harness._json import dump_json_bytes
from a13n_harness.identity import AgentInstanceContext

from .commands import (
    BoundProcessHandle,
    CommandRequest,
    PortObservation,
    PortTarget,
    ProcessControlResult,
    ProcessIdentity,
    ProcessInfo,
    ProcessOutputSnapshot,
    ProcessReadOutputResult,
    ProcessSignalResult,
    ProcessStartResult,
    ProcessWriteStdinResult,
    ShellExecResult,
)
from .extensions import EnvironmentRunExtension, EnvironmentRunExtensionContext
from .files import FileOperator
from .models import (
    DEFAULT_ENVIRONMENT_CLEANUP_TIMEOUT_SECONDS,
    DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS,
    ENVIRONMENT_ACTION_DISPATCH,
    EnvironmentAction,
    EnvironmentAvailability,
    EnvironmentBinding,
    EnvironmentBindingObservation,
    EnvironmentBindingRequest,
    EnvironmentBindingState,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentOperationFamily,
    EnvironmentOperationReceipt,
    EnvironmentPath,
    EnvironmentPermissionSet,
    EnvironmentReadinessRequirement,
    EnvironmentState,
    EnvironmentStateLimits,
    EnvironmentTopology,
    EnvironmentTopologyBindingChange,
    EnvironmentTopologyChange,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
)
from .providers import (
    BoundEnvironment,
    BoundEnvironmentProvider,
    BoundOutputOperations,
    BoundPortOperations,
    BoundProcessOperations,
    BoundShellOperations,
    EnvironmentProviderBinding,
    EnvironmentProviderOperations,
    EnvironmentRunBinding,
    EnvironmentTopologyController,
    EnvironmentTopologyObserver,
    FileScopeSelection,
)
from .retention import (
    BoundOutputCursor,
    BoundOutputReference,
    EnvironmentOutputCapture,
    EnvironmentOutputPolicy,
    EnvironmentOutputReadResult,
)
from .topology import DynamicTopologyController, DynamicTopologyObserver
from .virtual_files import VirtualFileOperator, _PreparedFile

if TYPE_CHECKING:
    from a13n_harness.model_context import ModelContextProjection, ModelContextProjectionRequest

_MAX_ENVIRONMENT_EXTENSION_ID_LENGTH = 200
_MAX_MODEL_CONTEXT_BINDINGS = 64
_MAX_MODEL_CONTEXT_BYTES = 64 * 1024


def _bounded_topology_json(payload: dict[str, JsonValue], max_bytes: int) -> str:
    bindings = cast(list[JsonValue], payload["bindings"])
    while True:
        encoded = dump_json_bytes(payload, sort_keys=True)
        if len(encoded) <= max_bytes:
            return encoded.decode("utf-8")
        if bindings:
            bindings.pop()
            payload["truncated"] = True
            continue
        minimal: dict[str, JsonValue] = {
            "topology_version": payload["topology_version"],
            "bindings": [],
            "truncated": True,
        }
        encoded = dump_json_bytes(minimal, sort_keys=True)
        if len(encoded) > max_bytes:
            raise EnvironmentError(
                "Environment topology byte limit cannot encode a minimal snapshot.",
                code="environment_projection_limit_invalid",
            )
        return encoded.decode("utf-8")


@dataclass(frozen=True, slots=True)
class _EnteredBinding:
    public: EnvironmentBinding
    provider: BoundEnvironmentProvider
    operations: EnvironmentProviderOperations
    environment_id: str


type _BindingVersionKey = tuple[str, int, str]


@dataclass(slots=True)
class _OwnedProviderScope:
    entered: _EnteredBinding
    scope: AbstractAsyncContextManager[BoundEnvironmentProvider]


def _validate_provider_artifacts(entered: _EnteredBinding, value: Any) -> None:
    bound_types = (
        BoundProcessHandle,
        BoundOutputReference,
        BoundOutputCursor,
        EnvironmentOperationReceipt,
    )
    if isinstance(value, bound_types):
        if (
            value.binding_id != entered.public.binding_id
            or value.binding_version != entered.public.binding_version
            or value.observed_generation != entered.public.descriptor.generation
        ):
            raise EnvironmentError(
                "Environment provider returned an artifact for another binding version.",
                code="environment_provider_failure",
            )
        return
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            _validate_provider_artifacts(entered, getattr(value, name))
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _validate_provider_artifacts(entered, item)
        return
    if isinstance(value, tuple | list):
        for item in value:
            _validate_provider_artifacts(entered, item)


def _capture_contiguous_prefix(capture: EnvironmentOutputCapture) -> bytes:
    if capture.inline is not None:
        return capture.inline
    expected = 0
    chunks: list[bytes] = []
    for segment in sorted(capture.preview, key=lambda item: item.start_offset):
        if segment.start_offset != expected:
            break
        chunks.append(segment.data)
        expected += len(segment.data)
    return b"".join(chunks)


def _validate_process_result_identity(expected: BoundProcessHandle, value: Any) -> None:
    if isinstance(value, BoundProcessHandle):
        if value != expected:
            raise EnvironmentError(
                "Environment provider retargeted a process operation to another handle.",
                code="environment_provider_failure",
            )
        return
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            _validate_process_result_identity(expected, getattr(value, name))
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _validate_process_result_identity(expected, item)
        return
    if isinstance(value, tuple | list):
        for item in value:
            _validate_process_result_identity(expected, item)


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
    ) -> AsyncGenerator[_EnteredBinding]:
        binding_id = selected.binding_id
        entered = self._environment.require_action(binding_id, action)
        if (
            selected.binding_version != entered.public.binding_version
            or selected.observed_generation != entered.public.descriptor.generation
        ):
            raise EnvironmentError("Output selector is stale.", code="environment_stale_binding")
        async with self._environment._operation_lease(entered, action, "outputs"):
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
        ):
            shell = entered.operations.shell
            if shell is None:
                raise EnvironmentError("Shell operation facet is unavailable.", code="environment_unsupported")
            result = await shell.exec(provider_request)
            _validate_provider_artifacts(entered, result)
            return result

    async def exec_captured(self, request: CommandRequest, *, alias: str | None = None) -> ShellExecResult:
        """Preflight and hold one binding version through foreground output materialization."""
        entered, provider_request = self._environment._prepare_command(request, alias=alias)
        for action in (EnvironmentAction.SHELL_EXEC, EnvironmentAction.OUTPUT_READ, EnvironmentAction.OUTPUT_RELEASE):
            selected = self._environment.require_action(entered.public.binding_id, action)
            if selected is not entered:
                raise EnvironmentError(
                    "Shell binding version changed before dispatch.", code="environment_stale_binding"
                )
        if entered.operations.outputs is None:
            raise EnvironmentError("Output operation facet is unavailable.", code="environment_unsupported")
        async with self._environment._operation_lease(
            entered,
            EnvironmentAction.SHELL_EXEC,
            "shell",
            timeout_seconds=provider_request.limits.wall_time_seconds,
        ):
            async with self._environment._operation_lease(
                entered,
                EnvironmentAction.OUTPUT_READ,
                "outputs",
            ):
                async with self._environment._operation_lease(
                    entered,
                    EnvironmentAction.OUTPUT_RELEASE,
                    "outputs",
                ):
                    shell = entered.operations.shell
                    if shell is None:
                        raise EnvironmentError(
                            "Shell operation facet is unavailable.",
                            code="environment_unsupported",
                        )
                    result = await shell.exec(provider_request)
                    _validate_provider_artifacts(entered, result)
                    stdout, stderr = await asyncio.gather(
                        self._materialize_capture(entered, result.output.stdout, provider_request.output_policy),
                        self._materialize_capture(entered, result.output.stderr, provider_request.output_policy),
                    )
                    return result.model_copy(update={"output": ProcessOutputSnapshot(stdout=stdout, stderr=stderr)})

    @staticmethod
    async def _materialize_capture(
        entered: _EnteredBinding,
        capture: EnvironmentOutputCapture,
        policy: EnvironmentOutputPolicy,
    ) -> EnvironmentOutputCapture:
        if capture.reference is None:
            return capture
        outputs = entered.operations.outputs
        if outputs is None:
            raise EnvironmentError("Output operation facet is unavailable.", code="environment_unsupported")
        read_policy = EnvironmentOutputPolicy(
            max_inline_bytes=max(1, min(capture.captured_bytes, policy.max_output_bytes)),
            max_output_bytes=max(1, policy.max_output_bytes),
            overflow="truncate",
        )
        data = _capture_contiguous_prefix(capture)
        materialized = False
        try:
            try:
                result = await outputs.read(capture.reference, start_offset=0, policy=read_policy)
                _validate_provider_artifacts(entered, result)
                data = b"".join(chunk.data for chunk in result.chunks)
                materialized = True
            except asyncio.CancelledError:
                raise
            except Exception:
                # The command has completed. A provider read failure must not turn
                # its known outcome and side effects into a retry-shaped failure.
                pass
        finally:
            try:
                receipt = await outputs.release(reference=capture.reference)
                _validate_provider_artifacts(entered, receipt)
            except Exception:
                # Output cleanup is best effort after the command and materialization
                # have completed; it cannot replace their known result.
                pass
        return capture.model_copy(
            update={
                "kind": "empty" if not data else "inline",
                "content_complete": (materialized and capture.content_complete and len(data) == capture.captured_bytes),
                "captured_bytes": len(data),
                "inline": data,
                "preview": (),
                "reference": None,
                "cursor": None,
                "available_start": 0,
                "available_end": len(data),
                "expires_at": None,
            }
        )


class _ProcessFacade:
    def __init__(self, environment: CompositeBoundEnvironment) -> None:
        self._environment = environment

    async def start(self, request: CommandRequest, *, alias: str | None = None) -> ProcessStartResult:
        entered, provider_request = self._environment._prepare_command(request, alias=alias)
        async with self._environment._operation_lease(
            entered,
            EnvironmentAction.PROCESS_START,
            "processes",
            timeout_seconds=request.limits.wall_time_seconds,
        ):
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
        ):
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
        ):
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
        ):
            ports = entered.operations.ports
            if ports is None:
                raise EnvironmentError("Port operation facet is unavailable.", code="environment_unsupported")
            call = getattr(ports, method)
            result = await call(target.model_copy(update={"alias": None}), **dict(kwargs or {}))
            _validate_provider_artifacts(entered, result)
            return result


class CompositeBoundEnvironment(BoundEnvironment):
    """One stable facade over a validated entered topology."""

    def __init__(
        self,
        *,
        run_id: str,
        instance: AgentInstanceContext,
        initial_request: EnvironmentTopologyRequest,
        topology: EnvironmentTopology,
        entered: Mapping[str, _EnteredBinding],
        owned_scopes: Mapping[_BindingVersionKey, _OwnedProviderScope],
        topology_limits: EnvironmentTopologyLimits,
        state_limits: EnvironmentStateLimits,
        observer: DynamicTopologyObserver,
        controller: DynamicTopologyController,
        extensions: tuple[tuple[str, EnvironmentRunExtension], ...],
    ) -> None:
        self._run_id = run_id
        self._instance = instance
        self._topology = topology
        self._entered = dict(entered)
        self._entered_by_version = {
            (item.public.binding_id, item.public.binding_version): item for item in entered.values()
        }
        self._owned_scopes = dict(owned_scopes)
        self._topology_limits = topology_limits
        self._state_limits = state_limits
        self._observer = observer
        self._controller = controller
        self._extensions = extensions
        self._extension_scopes: list[tuple[str, AbstractAsyncContextManager[None]]] = []
        self._activation_state = "not_started"
        self._restored_state_topology_version: int | None = None
        self._readiness_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._topology_apply_lock = asyncio.Lock()
        self._closed = False
        self._operation_tasks: dict[asyncio.Task[Any], int] = {}
        self._version_tasks: dict[_BindingVersionKey, dict[asyncio.Task[Any], int]] = {}
        self._version_drained: dict[_BindingVersionKey, asyncio.Event] = {}
        self._process_start_leases: dict[_BindingVersionKey, int] = {}
        self._active_process_handles: dict[_BindingVersionKey, set[BoundProcessHandle]] = {}
        self._readiness_tasks: dict[tuple[str, int, str, str], asyncio.Task[None]] = {}
        self._readiness_waiters: dict[asyncio.Task[None], int] = {}
        self._retirement_tasks: set[asyncio.Task[None]] = set()
        self._retirement_failures: list[BaseException] = []
        self._alias_owners = {item.alias: item.binding_id for item in topology.bindings}
        self._default_owner = topology.default_binding_id
        self._max_version_by_id = {item.binding_id: item.binding_version for item in topology.bindings}
        self._requests_by_id = {item.binding_id: item for item in initial_request.bindings}
        self._provider_identity_by_id = {
            item.public.binding_id: (item.public.provider_type, item.environment_id) for item in entered.values()
        }
        initial_digest = _topology_request_digest(initial_request)
        initial_receipt = EnvironmentTopologyChange(
            previous_version=topology.topology_version,
            current_version=topology.topology_version,
            request_digest=initial_digest,
            bindings=(),
        )
        self._receipts: dict[int, tuple[str, EnvironmentTopologyChange]] = {
            topology.topology_version: (initial_digest, initial_receipt)
        }
        self._committed_changes = 0
        self._files = VirtualFileOperator(
            self.resolve_path,
            self._prepare_file,
            self._virtualize_provider_path,
        )
        self._shell = _ShellFacade(self)
        self._processes = _ProcessFacade(self)
        self._ports = _PortFacade(self)
        self._outputs = _OutputFacade(self)

    @property
    def topology(self) -> EnvironmentTopology:
        return self._topology

    @property
    def restored_state_topology_version(self) -> int | None:
        return self._restored_state_topology_version

    @property
    def topology_observer(self) -> EnvironmentTopologyObserver:
        return self._observer

    @property
    def files(self) -> FileOperator:
        return self._files

    async def project_model_context(
        self,
        request: ModelContextProjectionRequest,
    ) -> ModelContextProjection:
        from a13n_harness.model_context import (
            ModelContextBlock,
            ModelContextPlacement,
            ModelContextProjection,
            ModelContextRequestKind,
        )

        if request.kind is not ModelContextRequestKind.INPUT:
            return ModelContextProjection()
        topology = self.topology
        selected = topology.bindings[:_MAX_MODEL_CONTEXT_BINDINGS]
        default = next((item for item in topology.bindings if item.binding_id == topology.default_binding_id), None)
        bindings: list[dict[str, JsonValue]] = []
        for binding in selected:
            availability = "unavailable"
            ready: list[str] = []
            reason: str | None = None
            try:
                observation = await self.describe(binding.binding_id)
                availability = observation.availability.status
                ready = sorted(observation.availability.ready_families)
                reason = observation.availability.reason_code
            except EnvironmentError:
                pass
            operations = sorted(
                {
                    ENVIRONMENT_ACTION_DISPATCH[action].family
                    for action in binding.permission_ceiling.operations
                    if ENVIRONMENT_ACTION_DISPATCH[action].family != "state"
                }
            )
            projected: dict[str, JsonValue] = {
                "alias": binding.alias,
                "root": (
                    "/workspace"
                    if binding.binding_id == topology.default_binding_id
                    else f"/environment/{binding.alias}"
                ),
                "operations": cast(JsonValue, operations),
                "availability": availability,
                "ready": cast(JsonValue, ready),
                "read_only": not any(
                    action.value.startswith(("environment.file.write", "environment.file.patch"))
                    or action.value
                    in {
                        "environment.file.mkdir",
                        "environment.file.move",
                        "environment.file.remove",
                        "environment.file.copy_destination",
                    }
                    for action in binding.permission_ceiling.operations
                ),
            }
            if reason is not None:
                projected["reason"] = reason
            bindings.append(projected)
        payload: dict[str, JsonValue] = {
            "topology_version": topology.topology_version,
            "restored_state_topology_version": self.restored_state_topology_version,
            "default_alias": default.alias if default is not None else None,
            "bindings": cast(JsonValue, bindings),
            "truncated": len(selected) < len(topology.bindings),
        }
        prefix = "Current Environment topology (trusted dynamic context):\n"
        content = prefix + _bounded_topology_json(
            payload,
            _MAX_MODEL_CONTEXT_BYTES - len(prefix.encode("utf-8")),
        )
        return ModelContextProjection(
            blocks=(
                ModelContextBlock(
                    source_id="a13n.environment-topology",
                    placement=ModelContextPlacement.INPUT_PREAMBLE,
                    content=content,
                ),
            )
        )

    def select_files(self, path: str) -> FileScopeSelection:
        selected = self.resolve_path(path)
        entered = self._entered_by_version.get((selected.binding_id, selected.binding_version))
        if entered is None:
            raise EnvironmentError(
                "Environment binding version is unavailable.",
                code="environment_stale_binding",
            )
        return FileScopeSelection(
            logical_path=path,
            resolved_path=selected,
            observed_generation=entered.public.descriptor.generation,
        )

    @asynccontextmanager
    async def open_files(self, selection: FileScopeSelection) -> AsyncGenerator[FileOperator]:
        if not isinstance(selection, FileScopeSelection):
            raise EnvironmentError("File scope selection is invalid.", code="environment_request_invalid")
        selected = selection.resolved_path
        entered = self._entered_by_version.get((selected.binding_id, selected.binding_version))
        if entered is None or entered.public.descriptor.generation != selection.observed_generation:
            raise EnvironmentError("File scope selection is stale.", code="environment_stale_binding")
        async with self._version_slot(entered):
            await self._ensure_provider_family(entered, "files")
            self._validate_live_observation(
                entered,
                entered.provider.availability,
                frozenset({"files"}),
            )
            if entered.operations.files is None:
                raise EnvironmentError("File operation facet is unavailable.", code="environment_unsupported")
            scoped = VirtualFileOperator(
                lambda path: self._resolve_scoped_file_path(path, entered, selection),
                lambda path, action: self._prepare_scoped_file(entered, path, action),
                lambda path, provider_path: self._virtualize_scoped_file_path(
                    entered,
                    selection,
                    path,
                    provider_path,
                ),
            )
            yield scoped

    @property
    def shell(self) -> BoundShellOperations:
        return self._shell

    @property
    def processes(self) -> BoundProcessOperations:
        return self._processes

    @property
    def ports(self) -> BoundPortOperations:
        return self._ports

    @property
    def outputs(self) -> BoundOutputOperations:
        return self._outputs

    async def activate(self) -> None:
        self._assert_open()
        if self._activation_state == "active":
            return
        if self._activation_state == "failed":
            raise EnvironmentError(
                "Environment run extension activation previously failed.",
                code="environment_extension_bind_failed",
            )
        if self._activation_state != "not_started":
            raise EnvironmentError("The Environment is closing.", code="environment_closed")
        self._activation_state = "activating"
        context = EnvironmentRunExtensionContext(
            run_id=self._run_id,
            instance=self._instance,
            environment=self,
        )
        try:
            for extension_id, extension in self._extensions:
                try:
                    scope = extension.bind(context=context)
                    if not isinstance(scope, AbstractAsyncContextManager):
                        raise TypeError("extension bind did not return an async context manager")
                    await scope.__aenter__()
                except Exception:
                    raise EnvironmentError(
                        "Environment run extension setup failed.",
                        code="environment_extension_bind_failed",
                        details={"extension_id": extension_id},
                    ) from None
                self._extension_scopes.append((extension_id, scope))
        except BaseException as primary:
            self._activation_state = "failed"
            extension_scopes = tuple(self._extension_scopes)
            self._extension_scopes.clear()
            try:
                await _await_cleanup_shielded(_close_extension_scopes(extension_scopes))
            except BaseException as cleanup_error:
                if cleanup_error is not primary:
                    raise BaseExceptionGroup(
                        "Environment run extension activation and cleanup failed",
                        [primary, cleanup_error],
                    ) from None
            raise
        self._controller.activate()
        self._activation_state = "active"

    @staticmethod
    def _binding_version_key(entered: _EnteredBinding) -> _BindingVersionKey:
        return (
            entered.public.binding_id,
            entered.public.binding_version,
            entered.public.descriptor.generation,
        )

    def _track_process_handle(self, handle: BoundProcessHandle, *, added: bool) -> None:
        key = (handle.binding_id, handle.binding_version, handle.observed_generation)
        if added:
            self._active_process_handles.setdefault(key, set()).add(handle)
            return
        handles = self._active_process_handles.get(key)
        if handles is None:
            return
        handles.discard(handle)
        if not handles:
            self._active_process_handles.pop(key, None)

    async def apply_topology(self, request: EnvironmentTopologyRequest) -> EnvironmentTopologyChange:
        """Prepare and atomically publish one complete Host-owned topology request."""
        supplied = request.bindings if isinstance(request, EnvironmentTopologyRequest) else ()
        claimed, reused = _claim_candidate_transfers(supplied)
        if reused:
            primary = EnvironmentError(
                "Environment provider binding was already transferred.",
                code="environment_binding_reused",
            )
            try:
                await _await_cleanup_shielded(_discard_candidate_instances(claimed))
            except BaseException as cleanup:
                raise BaseExceptionGroup(
                    "Environment candidate reuse rejection and cleanup failed",
                    [primary, cleanup],
                ) from None
            raise primary
        normalized: EnvironmentTopologyRequest | None = None
        prepared_scopes: dict[_BindingVersionKey, _OwnedProviderScope] = {}
        successfully_entered: set[int] = set()
        committed = False
        async with self._topology_apply_admission(supplied):
            try:
                normalized = _normalize_topology_request(request)
                digest = _topology_request_digest(normalized)
                current_version = self._topology.topology_version
                if self._closed or not self._controller.can_apply:
                    raise EnvironmentError("The Environment run is not active.", code="run_not_active")
                if normalized.topology_version < current_version:
                    raise EnvironmentError("Environment topology version is stale.", code="topology_stale")
                if normalized.topology_version == current_version:
                    stored_digest, receipt = self._receipts[current_version]
                    if digest != stored_digest:
                        raise EnvironmentError(
                            "Environment topology version has a conflicting request.",
                            code="topology_conflict",
                        )
                    if any(item.provider_binding is not None for item in normalized.bindings):
                        raise EnvironmentError(
                            "A topology replay cannot transfer provider bindings.",
                            code="environment_topology_invalid",
                        )
                    return receipt
                if self._committed_changes >= self._topology_limits.max_committed_changes:
                    raise EnvironmentError(
                        "Environment topology committed-change limit is exhausted.",
                        code="environment_topology_limit",
                    )

                candidate_requests = self._validate_dynamic_request(normalized)
                for requested in candidate_requests:
                    candidate = requested.provider_binding
                    assert isinstance(candidate, EnvironmentProviderBinding)
                    scope = candidate.bind(
                        run_id=self._run_id,
                        instance=self._instance,
                        binding_id=requested.binding_id,
                        binding_version=requested.binding_version,
                    )
                    async with asyncio.timeout(DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS):
                        provider = await scope.__aenter__()
                    successfully_entered.add(id(candidate))
                    try:
                        entered = _validate_entered(requested, candidate, provider)
                    except BaseException as primary:
                        try:
                            await _await_cleanup_shielded(_close_provider_scopes([scope]))
                        except BaseException as cleanup:
                            raise BaseExceptionGroup(
                                "Environment provider validation and scope cleanup failed",
                                [primary, cleanup],
                            ) from None
                        raise
                    key = self._binding_version_key(entered)
                    prepared_scopes[key] = _OwnedProviderScope(entered=entered, scope=scope)

                proposed: dict[str, _EnteredBinding] = {}
                for requested in normalized.bindings:
                    current = self._entered.get(requested.binding_id)
                    if current is not None and current.public.binding_version == requested.binding_version:
                        proposed[requested.binding_id] = current
                    else:
                        prepared = next(
                            item
                            for item in prepared_scopes.values()
                            if item.entered.public.binding_id == requested.binding_id
                        )
                        proposed[requested.binding_id] = prepared.entered
                topology = EnvironmentTopology(
                    topology_version=normalized.topology_version,
                    bindings=tuple(item.public for item in proposed.values()),
                    default_binding_id=normalized.default_binding_id,
                )
                change = _topology_change(self._topology, topology, digest)
                retiring = {
                    self._binding_version_key(old): old
                    for binding_id, old in self._entered.items()
                    if binding_id not in proposed
                    or proposed[binding_id].public.binding_version != old.public.binding_version
                }

                async with self._operation_lock:
                    if self._closed or not self._controller.can_apply:
                        raise EnvironmentError("The Environment run is not active.", code="run_not_active")
                    in_use = [
                        key
                        for key in retiring
                        if self._active_process_handles.get(key) or self._process_start_leases.get(key, 0) > 0
                    ]
                    if in_use:
                        raise EnvironmentError(
                            "Environment topology update would retire an active process handle.",
                            code="topology_in_use",
                        )
                    for key, owned in prepared_scopes.items():
                        self._owned_scopes[key] = owned
                        self._entered_by_version[
                            (owned.entered.public.binding_id, owned.entered.public.binding_version)
                        ] = owned.entered
                    retired_scopes = {key: self._owned_scopes.pop(key) for key in retiring if key in self._owned_scopes}
                    self._topology = topology
                    self._entered = proposed
                    self._requests_by_id = {item.binding_id: item for item in normalized.bindings}
                    for item in topology.bindings:
                        self._alias_owners.setdefault(item.alias, item.binding_id)
                        self._max_version_by_id[item.binding_id] = item.binding_version
                    if topology.default_binding_id is not None and self._default_owner is None:
                        self._default_owner = topology.default_binding_id
                    for owned in prepared_scopes.values():
                        self._provider_identity_by_id.setdefault(
                            owned.entered.public.binding_id,
                            (owned.entered.public.provider_type, owned.entered.environment_id),
                        )
                    self._committed_changes += 1
                    self._receipts[topology.topology_version] = (digest, change)
                    self._observer.publish(change)
                committed = True
                for key, owned in retired_scopes.items():
                    self._schedule_retirement(key, owned)
                return change
            finally:
                if not committed:
                    cleanup_requests = normalized.bindings if normalized is not None else supplied
                    active_error = sys.exception()
                    try:
                        await _await_cleanup_shielded(
                            _cleanup_topology_candidates(
                                requests=cleanup_requests,
                                prepared_scopes=prepared_scopes,
                                successfully_entered=successfully_entered,
                            )
                        )
                    except BaseException as cleanup_error:
                        if active_error is not None and active_error is not cleanup_error:
                            raise BaseExceptionGroup(
                                "Environment topology apply and candidate cleanup failed",
                                [active_error, cleanup_error],
                            ) from None
                        raise

    @asynccontextmanager
    async def _topology_apply_admission(
        self,
        supplied: tuple[EnvironmentBindingRequest, ...],
    ) -> AsyncGenerator[None]:
        try:
            await self._topology_apply_lock.acquire()
        except BaseException as primary:
            try:
                await _await_cleanup_shielded(_discard_candidates(supplied, set()))
            except BaseException as cleanup:
                raise BaseExceptionGroup(
                    "Environment topology admission and candidate cleanup failed",
                    [primary, cleanup],
                ) from None
            raise
        try:
            yield
        finally:
            self._topology_apply_lock.release()

    def _validate_dynamic_request(
        self,
        request: EnvironmentTopologyRequest,
    ) -> tuple[EnvironmentBindingRequest, ...]:
        _validate_request(request, self._topology_limits)
        candidates: list[EnvironmentBindingRequest] = []
        for requested in request.bindings:
            owner = self._alias_owners.get(requested.alias)
            if owner is not None and owner != requested.binding_id:
                raise EnvironmentError(
                    "Environment alias is owned by another historical binding.",
                    code="environment_topology_invalid",
                )
            current = self._entered.get(requested.binding_id)
            maximum = self._max_version_by_id.get(requested.binding_id, 0)
            if current is not None and requested.binding_version == current.public.binding_version:
                retained_request = self._requests_by_id[requested.binding_id]
                if requested.provider_binding is not None:
                    raise EnvironmentError(
                        "A retained topology binding cannot transfer a provider binding.",
                        code="environment_topology_invalid",
                    )
                if (
                    requested.alias != retained_request.alias
                    or requested.permission_ceiling != retained_request.permission_ceiling
                    or requested.default_working_directory != retained_request.default_working_directory
                ):
                    raise EnvironmentError(
                        "A retained topology binding is not immutable.",
                        code="environment_topology_invalid",
                    )
                continue
            if requested.binding_version <= maximum:
                raise EnvironmentError(
                    "Environment binding version is not monotonic.",
                    code="environment_topology_invalid",
                )
            candidate = requested.provider_binding
            if not isinstance(candidate, EnvironmentProviderBinding):
                raise EnvironmentError(
                    "An added or refreshed binding requires a fresh provider binding.",
                    code="environment_topology_invalid",
                )
            identity = self._provider_identity_by_id.get(requested.binding_id)
            if identity is not None and (
                candidate.provider_type != identity[0] or candidate.environment_id != identity[1]
            ):
                raise EnvironmentError(
                    "Environment binding provider identity is incompatible with its history.",
                    code="environment_topology_invalid",
                )
            candidates.append(requested)
        if request.default_binding_id is not None:
            if self._default_owner is not None and request.default_binding_id != self._default_owner:
                raise EnvironmentError(
                    "The /workspace selector is owned by another historical binding.",
                    code="environment_topology_invalid",
                )
        return tuple(candidates)

    def _schedule_retirement(self, key: _BindingVersionKey, owned: _OwnedProviderScope) -> None:
        task = asyncio.create_task(self._retire_scope(key, owned))
        self._retirement_tasks.add(task)
        task.add_done_callback(self._retirement_finished)

    def _retirement_finished(self, task: asyncio.Task[None]) -> None:
        self._retirement_tasks.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                self._retirement_failures.append(error)

    async def _retire_scope(self, key: _BindingVersionKey, owned: _OwnedProviderScope) -> None:
        while True:
            async with self._operation_lock:
                active = bool(self._version_tasks.get(key))
                drained = self._version_drained.setdefault(key, asyncio.Event())
                if not active:
                    drained.set()
            if not active:
                break
            await drained.wait()
        scopes = [owned.scope]
        await _close_provider_scopes(scopes)
        async with self._operation_lock:
            self._version_drained.pop(key, None)

    def resolve_path(self, path: str, *, alias: str | None = None) -> EnvironmentPath:
        """Resolve one virtual or relative path without native-path fallback."""
        self._assert_open()
        if not path or "\x00" in path:
            raise EnvironmentError("Environment path is invalid.", code="environment_request_invalid")
        segments = path.split("/")
        if any(segment == ".." or (segment == "." and path != ".") for segment in segments):
            raise EnvironmentError("Environment path traversal is invalid.", code="environment_request_invalid")

        by_alias = {entered.public.alias: entered for entered in self._entered.values()}
        selected: _EnteredBinding | None = None
        provider_path: str
        if path.startswith("/workspace") and (path == "/workspace" or path.startswith("/workspace/")):
            if alias is not None:
                selected_alias = by_alias.get(alias)
                if selected_alias is None or selected_alias.public.binding_id != self._topology.default_binding_id:
                    raise EnvironmentError(
                        "The alias and virtual path select different bindings.",
                        code="environment_selection_invalid",
                    )
            selected = self._entered.get(self._topology.default_binding_id or "")
            provider_path = path.removeprefix("/workspace") or "/"
        elif path.startswith("/environment/"):
            remainder = path.removeprefix("/environment/")
            selected_alias, separator, tail = remainder.partition("/")
            selected = by_alias.get(selected_alias)
            if alias is not None and alias != selected_alias:
                raise EnvironmentError(
                    "The alias and virtual path select different bindings.",
                    code="environment_selection_invalid",
                )
            provider_path = f"/{tail}" if separator else "/"
        elif path.startswith("/"):
            raise EnvironmentError(
                "Absolute paths must use /workspace or /environment/{alias}.",
                code="environment_selection_invalid",
            )
        else:
            if alias is not None:
                selected = by_alias.get(alias)
            else:
                selected = self._entered.get(self._topology.default_binding_id or "")
            if selected is None:
                raise EnvironmentError(
                    "A relative path requires an available selected or default binding.",
                    code="environment_selection_invalid",
                )
            base = selected.public.default_working_directory or "/"
            if not base.startswith("/") or any(segment in {".", ".."} for segment in base.split("/")):
                raise EnvironmentError(
                    "The binding default working directory is invalid.",
                    code="environment_provider_failure",
                )
            provider_path = base if path == "." else f"{base.rstrip('/')}/{path}"

        if selected is None:
            raise EnvironmentError(
                "The selected Environment binding is unavailable.",
                code="environment_selection_invalid",
                retry_hint="dependency_change",
            )
        return EnvironmentPath(
            binding_id=selected.public.binding_id,
            binding_version=selected.public.binding_version,
            path=provider_path,
        )

    def _select_entered(self, alias: str | None) -> _EnteredBinding:
        self._assert_open()
        if alias is None:
            entered = self._entered.get(self._topology.default_binding_id or "")
        else:
            entered = next(
                (item for item in self._entered.values() if item.public.alias == alias),
                None,
            )
        if entered is None:
            raise EnvironmentError(
                "The selected Environment binding is unavailable.",
                code="environment_selection_invalid",
            )
        return entered

    def _prepare_command(
        self,
        request: CommandRequest,
        *,
        alias: str | None,
    ) -> tuple[_EnteredBinding, CommandRequest]:
        if request.cwd is None:
            entered = self._select_entered(alias)
            cwd = entered.public.default_working_directory or "/"
        else:
            selected = self.resolve_path(request.cwd, alias=alias)
            entered = self._entered[selected.binding_id]
            cwd = selected.path
        limits = request.limits
        ceiling = entered.public.descriptor.limits.get("max_wall_time_seconds")
        ceiling_value: float | None = None
        if not isinstance(ceiling, bool) and isinstance(ceiling, int | float):
            try:
                candidate = float(ceiling)
            except OverflowError:
                pass
            else:
                if math.isfinite(candidate) and candidate > 0:
                    ceiling_value = candidate
        if ceiling_value is not None:
            requested = limits.wall_time_seconds
            effective = ceiling_value if requested is None else min(requested, ceiling_value)
            limits = limits.model_copy(update={"wall_time_seconds": effective})
        return entered, request.model_copy(update={"cwd": cwd, "limits": limits})

    def _entered_for_process_identity(self, identity: ProcessIdentity) -> _EnteredBinding:
        if not isinstance(identity, ProcessIdentity):
            raise EnvironmentError("Process identity is invalid.", code="environment_request_invalid")
        matches = tuple(
            item
            for item in self._entered.values()
            if item.public.provider_type == identity.provider_type and item.environment_id == identity.environment_id
        )
        if not matches:
            raise EnvironmentError(
                "The process Environment instance is not attached.",
                code="environment_selection_invalid",
            )
        if len(matches) != 1:
            raise EnvironmentError(
                "The process Environment instance is attached more than once.",
                code="environment_conflict",
            )
        entered = matches[0]
        if entered.public.descriptor.generation != identity.generation:
            raise EnvironmentError(
                "The process belongs to another Environment generation.",
                code="environment_process_generation_mismatch",
            )
        return entered

    def _entered_for_handle(self, handle: BoundProcessHandle) -> _EnteredBinding:
        if not isinstance(handle, BoundProcessHandle):
            raise EnvironmentError("Process handle is invalid.", code="environment_request_invalid")
        entered = self._entered.get(handle.binding_id)
        if entered is None:
            raise EnvironmentError("Process binding is unavailable.", code="environment_selection_invalid")
        if (
            handle.binding_version != entered.public.binding_version
            or handle.observed_generation != entered.public.descriptor.generation
            or handle.identity.provider_type != entered.public.provider_type
            or handle.identity.environment_id != entered.environment_id
            or handle.identity.generation != entered.public.descriptor.generation
        ):
            raise EnvironmentError("Process handle is stale.", code="environment_stale_binding")
        return entered

    def require_action(self, binding_id: str, action: EnvironmentAction) -> _EnteredBinding:
        """Fail closed unless one exact catalog action is effective for the binding."""
        self._assert_open()
        if not isinstance(action, EnvironmentAction):
            raise EnvironmentError(
                "Environment authorization requires an exact catalog action.",
                code="environment_request_invalid",
            )
        entered = self._entered.get(binding_id)
        if entered is None:
            raise EnvironmentError("Unknown Environment binding.", code="environment_selection_invalid")
        if action not in entered.public.permission_ceiling.operations:
            raise EnvironmentError(
                "Environment operation is denied by the binding permission ceiling.",
                code="environment_denied",
                details={"action": action.value, "binding_id": binding_id},
            )
        return entered

    def _resolve_scoped_file_path(
        self,
        path: str,
        entered: _EnteredBinding,
        selection: FileScopeSelection,
    ) -> EnvironmentPath:
        if not isinstance(path, str) or not path or "\x00" in path:
            raise EnvironmentError("Environment path is invalid.", code="environment_request_invalid")
        segments = path.split("/")
        if any(segment == ".." or (segment == "." and path != ".") for segment in segments):
            raise EnvironmentError("Environment path traversal is invalid.", code="environment_request_invalid")
        selected_as_default = not selection.logical_path.startswith("/environment/")
        if path.startswith("/workspace") and (path == "/workspace" or path.startswith("/workspace/")):
            if not selected_as_default:
                raise EnvironmentError(
                    "The scoped file path selects another binding.",
                    code="environment_selection_invalid",
                )
            provider_path = path.removeprefix("/workspace") or "/"
        elif path.startswith("/environment/"):
            remainder = path.removeprefix("/environment/")
            alias, separator, tail = remainder.partition("/")
            if alias != entered.public.alias:
                raise EnvironmentError(
                    "The scoped file path selects another binding.",
                    code="environment_selection_invalid",
                )
            provider_path = f"/{tail}" if separator else "/"
        elif path.startswith("/"):
            raise EnvironmentError(
                "Absolute paths must use /workspace or /environment/{alias}.",
                code="environment_selection_invalid",
            )
        else:
            base = entered.public.default_working_directory or "/"
            if not base.startswith("/") or any(segment in {".", ".."} for segment in base.split("/")):
                raise EnvironmentError(
                    "The binding default working directory is invalid.",
                    code="environment_provider_failure",
                )
            provider_path = base if path == "." else f"{base.rstrip('/')}/{path}"
        return EnvironmentPath(
            binding_id=entered.public.binding_id,
            binding_version=entered.public.binding_version,
            path=provider_path,
        )

    @asynccontextmanager
    async def _prepare_scoped_file(
        self,
        entered: _EnteredBinding,
        selected: EnvironmentPath,
        action: EnvironmentAction,
    ) -> AsyncGenerator[_PreparedFile]:
        if (
            selected.binding_id != entered.public.binding_id
            or selected.binding_version != entered.public.binding_version
        ):
            raise EnvironmentError(
                "The scoped file path selects another binding version.",
                code="environment_selection_invalid",
            )
        if action not in entered.public.permission_ceiling.operations:
            raise EnvironmentError(
                "Environment operation is denied by the binding permission ceiling.",
                code="environment_denied",
                details={"action": action.value, "binding_id": entered.public.binding_id},
            )
        try:
            async with asyncio.timeout(DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS):
                self._validate_live_observation(
                    entered,
                    entered.provider.availability,
                    frozenset({"files"}),
                )
                if entered.operations.files is None:
                    raise EnvironmentError("File operation facet is unavailable.", code="environment_unsupported")
                yield _PreparedFile(
                    selected=selected,
                    observed_generation=entered.public.descriptor.generation,
                    backend=entered.operations.files,
                    validate_result=lambda value: _validate_provider_artifacts(entered, value),
                )
        except TimeoutError as exc:
            raise EnvironmentError(
                "Environment operation timed out.",
                code="environment_timeout",
                details={"timeout_seconds": DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS},
                retry_hint="dependency_change",
            ) from exc

    @staticmethod
    def _virtualize_scoped_file_path(
        entered: _EnteredBinding,
        selection: FileScopeSelection,
        selected: EnvironmentPath,
        provider_path: str,
    ) -> str:
        if (
            selected.binding_id != entered.public.binding_id
            or selected.binding_version != entered.public.binding_version
        ):
            raise EnvironmentError(
                "Provider returned a path for another scoped binding version.",
                code="environment_provider_failure",
            )
        suffix = provider_path if provider_path.startswith("/") else f"/{provider_path}"
        root = (
            "/workspace"
            if not selection.logical_path.startswith("/environment/")
            else f"/environment/{entered.public.alias}"
        )
        return f"{root}{suffix}" if suffix != "/" else root

    @asynccontextmanager
    async def _prepare_file(
        self,
        selected: EnvironmentPath,
        action: EnvironmentAction,
    ) -> AsyncGenerator[_PreparedFile]:
        entered = self._entered_by_version.get((selected.binding_id, selected.binding_version))
        if entered is None:
            raise EnvironmentError(
                "Environment binding version is unavailable.",
                code="environment_stale_binding",
            )
        async with self._operation_lease(entered, action, "files"):
            if entered.operations.files is None:
                raise EnvironmentError("File operation facet is unavailable.", code="environment_unsupported")
            yield _PreparedFile(
                selected=selected,
                observed_generation=entered.public.descriptor.generation,
                backend=entered.operations.files,
                validate_result=lambda value: _validate_provider_artifacts(entered, value),
            )

    def _virtualize_provider_path(self, selected: EnvironmentPath, provider_path: str) -> str:
        entered = self._entered_by_version[(selected.binding_id, selected.binding_version)]
        suffix = provider_path if provider_path.startswith("/") else f"/{provider_path}"
        if self._topology.default_binding_id == selected.binding_id:
            return f"/workspace{suffix}" if suffix != "/" else "/workspace"
        root = f"/environment/{entered.public.alias}"
        return f"{root}{suffix}" if suffix != "/" else root

    def _assert_open(self) -> None:
        if self._closed:
            raise EnvironmentError("The Environment is closed.", code="environment_closed")

    def _validate_live_observation(
        self,
        entered: _EnteredBinding,
        availability: object,
        requested: frozenset[EnvironmentOperationFamily] = frozenset(),
    ) -> None:
        if not isinstance(availability, EnvironmentAvailability):
            raise EnvironmentError(
                "Provider returned an invalid availability observation.",
                code="environment_provider_failure",
                details={"binding_id": entered.public.binding_id},
            )
        if not availability.ready_families <= entered.public.descriptor.operation_families:
            raise EnvironmentError(
                "Provider readiness drifted beyond its published descriptor.",
                code="environment_provider_failure",
                details={"binding_id": entered.public.binding_id},
            )
        if requested and availability.status == "unavailable":
            raise EnvironmentError(
                "Provider is unavailable for the requested operation.",
                code="environment_unavailable",
                details={"binding_id": entered.public.binding_id, "reason_code": availability.reason_code},
                retry_hint="dependency_change",
            )
        if not requested <= availability.ready_families:
            raise EnvironmentError(
                "Provider returned before the requested families became ready.",
                code="environment_unavailable",
                details={"binding_id": entered.public.binding_id},
                retry_hint="dependency_change",
            )

    async def describe(self, binding_id: str) -> EnvironmentBindingObservation:
        self._assert_open()
        entered = self._entered.get(binding_id)
        if entered is None:
            raise EnvironmentError(
                f"Unknown Environment binding: {binding_id!r}.",
                code="environment_selection_invalid",
                details={"binding_id": binding_id},
            )
        availability = entered.provider.availability
        self._validate_live_observation(entered, availability)
        return EnvironmentBindingObservation(binding=entered.public, availability=availability)

    async def ensure_ready(self, requirement: EnvironmentReadinessRequirement) -> None:
        async with self._operation_slot():
            await self._ensure_ready(requirement)

    async def _ensure_ready(self, requirement: EnvironmentReadinessRequirement) -> None:
        requested = requirement.operations
        if requirement.binding_ids is None:
            selected = [
                entered
                for entered in self._entered.values()
                if entered.public.descriptor.operation_families & requested
            ]
        else:
            selected = []
            for binding_id in requirement.binding_ids:
                entered = self._entered.get(binding_id)
                if entered is None:
                    raise EnvironmentError(
                        f"Unknown Environment binding: {binding_id!r}.",
                        code="environment_selection_invalid",
                        details={"binding_id": binding_id},
                    )
                if not entered.public.descriptor.operation_families & requested:
                    raise EnvironmentError(
                        "An explicitly selected binding advertises none of the requested families.",
                        code="environment_readiness_invalid",
                        details={"binding_id": binding_id},
                    )
                selected.append(entered)

        covered = frozenset().union(*(entered.public.descriptor.operation_families & requested for entered in selected))
        if not selected or covered != requested:
            raise EnvironmentError(
                "The selected topology does not cover every requested operation family.",
                code="environment_unavailable",
                details={"missing": [str(item) for item in sorted(requested - covered)]},
                retry_hint="dependency_change",
            )

        async def wait_one(entered: _EnteredBinding) -> None:
            async with self._version_slot(entered):
                families = frozenset(entered.public.descriptor.operation_families & requested)
                await asyncio.gather(*(self._ensure_provider_family(entered, family) for family in families))
                current = entered.provider.descriptor
                if not isinstance(current, EnvironmentDescriptor):
                    raise EnvironmentError(
                        "Provider descriptor became invalid during readiness.",
                        code="environment_provider_failure",
                    )
                if current.generation != entered.public.descriptor.generation:
                    raise EnvironmentError(
                        "Provider generation changed during readiness.",
                        code="environment_stale_binding",
                        details={"binding_id": entered.public.binding_id},
                        retry_hint="dependency_change",
                    )
                if current != entered.public.descriptor:
                    raise EnvironmentError(
                        "Provider descriptor changed in place during readiness.",
                        code="environment_provider_failure",
                        details={"binding_id": entered.public.binding_id},
                    )
                self._validate_live_observation(entered, entered.provider.availability, families)

        timeout = requirement.timeout_seconds or DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS
        try:
            async with asyncio.timeout(timeout):
                await asyncio.gather(*(wait_one(entered) for entered in selected))
        except TimeoutError as exc:
            raise EnvironmentError(
                "Environment readiness timed out.",
                code="environment_timeout",
                details={"timeout_seconds": timeout},
                retry_hint="dependency_change",
            ) from exc
        except asyncio.CancelledError:
            raise
        except EnvironmentError:
            raise
        except Exception as exc:
            raise EnvironmentError(
                "Environment provider readiness failed.",
                code="environment_provider_failure",
                retry_hint="dependency_change",
            ) from exc

    @asynccontextmanager
    async def _operation_slot(self) -> AsyncGenerator[None]:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Environment operations require an asyncio task")
        async with self._operation_lock:
            if self._closed:
                raise EnvironmentError("The Environment is closed.", code="environment_closed")
            self._operation_tasks[task] = self._operation_tasks.get(task, 0) + 1
        try:
            yield
        finally:
            async with self._operation_lock:
                remaining = self._operation_tasks.get(task, 0) - 1
                if remaining > 0:
                    self._operation_tasks[task] = remaining
                else:
                    self._operation_tasks.pop(task, None)

    @asynccontextmanager
    async def _version_slot(self, entered: _EnteredBinding) -> AsyncGenerator[None]:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Environment operations require an asyncio task")
        key = self._binding_version_key(entered)
        async with self._operation_lock:
            if self._closed:
                raise EnvironmentError("The Environment is closed.", code="environment_closed")
            if self._entered.get(entered.public.binding_id) is not entered:
                raise EnvironmentError("Environment binding is stale.", code="environment_stale_binding")
            self._operation_tasks[task] = self._operation_tasks.get(task, 0) + 1
            self._register_version_task_locked(key, task)
        try:
            yield
        finally:
            async with self._operation_lock:
                self._release_task_count(self._operation_tasks, task)
                self._release_version_task_locked(key, task)

    def _register_version_task_locked(self, key: _BindingVersionKey, task: asyncio.Task[Any]) -> None:
        task_counts = self._version_tasks.setdefault(key, {})
        task_counts[task] = task_counts.get(task, 0) + 1
        self._version_drained.setdefault(key, asyncio.Event()).clear()

    def _release_version_task_locked(self, key: _BindingVersionKey, task: asyncio.Task[Any]) -> None:
        task_counts = self._version_tasks.get(key)
        if task_counts is None:
            return
        self._release_task_count(task_counts, task)
        if not task_counts:
            self._version_tasks.pop(key, None)
            self._version_drained.setdefault(key, asyncio.Event()).set()

    @staticmethod
    def _release_task_count(tasks: dict[asyncio.Task[Any], int], task: asyncio.Task[Any]) -> None:
        remaining = tasks.get(task, 0) - 1
        if remaining > 0:
            tasks[task] = remaining
        else:
            tasks.pop(task, None)

    @asynccontextmanager
    async def _operation_lease(
        self,
        entered: _EnteredBinding,
        action: EnvironmentAction,
        family: EnvironmentOperationFamily,
        *,
        timeout_seconds: float | None = None,
        timeout_code: str = "environment_timeout",
    ) -> AsyncGenerator[None]:
        timeout = (
            DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS
            if timeout_seconds is None
            else max(
                DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS,
                timeout_seconds + DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS,
            )
        )
        key = self._binding_version_key(entered)
        if action not in entered.public.permission_ceiling.operations:
            raise EnvironmentError(
                "Environment operation is denied by the binding permission ceiling.",
                code="environment_denied",
                details={"action": action.value, "binding_id": entered.public.binding_id},
            )
        async with self._version_slot(entered):
            if action is EnvironmentAction.PROCESS_START:
                self._process_start_leases[key] = self._process_start_leases.get(key, 0) + 1
            try:
                try:
                    async with asyncio.timeout(timeout):
                        await self._ensure_provider_family(entered, family)
                        self._validate_live_observation(
                            entered,
                            entered.provider.availability,
                            frozenset({family}),
                        )
                        yield
                except TimeoutError as exc:
                    raise EnvironmentError(
                        "Environment operation timed out.",
                        code=timeout_code,
                        details={"timeout_seconds": timeout},
                        retry_hint="dependency_change",
                    ) from exc
            finally:
                if action is EnvironmentAction.PROCESS_START:
                    remaining = self._process_start_leases.get(key, 0) - 1
                    if remaining > 0:
                        self._process_start_leases[key] = remaining
                    else:
                        self._process_start_leases.pop(key, None)

    async def _ensure_provider_family(
        self,
        entered: _EnteredBinding,
        family: EnvironmentOperationFamily,
    ) -> None:
        key = (
            entered.public.binding_id,
            entered.public.binding_version,
            entered.public.descriptor.generation,
            family,
        )
        version_key = self._binding_version_key(entered)
        async with self._readiness_lock:
            if self._closed:
                raise EnvironmentError("The Environment is closed.", code="environment_closed")
            task = self._readiness_tasks.get(key)
            if task is None:
                async with self._operation_lock:
                    if self._closed:
                        raise EnvironmentError("The Environment is closed.", code="environment_closed")
                    if self._entered.get(entered.public.binding_id) is not entered:
                        raise EnvironmentError("Environment binding is stale.", code="environment_stale_binding")
                    task = asyncio.create_task(entered.provider.ensure_ready(frozenset({family})))
                    self._register_version_task_locked(version_key, task)
                    task.add_done_callback(
                        lambda completed, captured=version_key: self._readiness_worker_finished(captured, completed)
                    )
                self._readiness_tasks[key] = task
            self._readiness_waiters[task] = self._readiness_waiters.get(task, 0) + 1
        try:
            await asyncio.shield(task)
        finally:
            async with self._readiness_lock:
                remaining = self._readiness_waiters.get(task, 0) - 1
                if remaining > 0:
                    self._readiness_waiters[task] = remaining
                else:
                    self._readiness_waiters.pop(task, None)

                if self._readiness_tasks.get(key) is task:
                    if task.done():
                        self._readiness_tasks.pop(key, None)
                        if not task.cancelled():
                            task.exception()
                    elif remaining <= 0:
                        self._readiness_tasks.pop(key, None)
                        task.cancel()

    def _readiness_worker_finished(self, key: _BindingVersionKey, task: asyncio.Task[Any]) -> None:
        release = asyncio.create_task(self._release_readiness_version(key, task))
        _supervise_cleanup_task(release)

    async def _release_readiness_version(self, key: _BindingVersionKey, task: asyncio.Task[Any]) -> None:
        async with self._operation_lock:
            self._release_version_task_locked(key, task)

    async def _close(self) -> None:
        current = asyncio.current_task()
        failures: list[BaseException] = []
        self._activation_state = "closing"
        extension_scopes = tuple(self._extension_scopes)
        self._extension_scopes.clear()
        async with self._topology_apply_lock:
            try:
                await _close_extension_scopes(extension_scopes)
            except BaseException as exc:
                failures.append(exc)
            async with self._operation_lock:
                self._closed = True
                operation_tasks = tuple(task for task in self._operation_tasks if task is not current)
        async with self._readiness_lock:
            readiness_tasks = tuple(self._readiness_tasks.values())
            self._readiness_tasks.clear()
            self._readiness_waiters.clear()
        tasks = tuple(dict.fromkeys((*operation_tasks, *readiness_tasks)))
        for task in tasks:
            if not task.done():
                task.cancel()
        pending: set[asyncio.Task[Any]] = set()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=DEFAULT_ENVIRONMENT_CLEANUP_TIMEOUT_SECONDS)
            for task in done:
                if not task.cancelled():
                    task.exception()
            if pending:
                failures.append(
                    EnvironmentError(
                        "Environment operations did not stop before cleanup deadline.",
                        code="environment_cleanup_timeout",
                        details={"pending_operations": len(pending)},
                    )
                )

        active_scopes = [owned.scope for owned in self._owned_scopes.values()]
        self._owned_scopes.clear()
        if pending:
            continuation = asyncio.create_task(_close_provider_scopes_after_tasks(tuple(pending), active_scopes))
            _supervise_cleanup_task(continuation)
        else:
            retirement = tuple(self._retirement_tasks)
            if retirement:
                await asyncio.gather(*retirement, return_exceptions=True)
            try:
                await _close_provider_scopes(active_scopes)
            except BaseException as exc:
                failures.append(exc)
        failures.extend(self._retirement_failures)
        if failures:
            raise BaseExceptionGroup("Environment aggregate cleanup failed", failures)

    async def export_state(self) -> EnvironmentState:
        self._assert_open()
        topology = self._topology
        eligible = [
            self._entered[item.binding_id]
            for item in topology.bindings
            if "state" in item.descriptor.operation_families
        ]
        if len(eligible) > self._state_limits.max_binding_entries:
            raise EnvironmentError("Environment state has too many entries.", code="state_too_large")
        entries: dict[str, EnvironmentBindingState] = {}
        remaining = self._state_limits.max_aggregate_encoded_bytes
        try:
            async with asyncio.timeout(self._state_limits.export_timeout_seconds):
                for entered in eligible:
                    async with self._operation_lease(
                        entered,
                        EnvironmentAction.STATE_EXPORT,
                        "state",
                        timeout_seconds=self._state_limits.export_timeout_seconds,
                        timeout_code="state_timeout",
                    ):
                        budget = min(self._state_limits.max_binding_encoded_bytes, remaining)
                        state = await entered.provider.export_state(max_bytes=budget)
                        if state is None:
                            continue
                        if state.provider_type != entered.public.provider_type:
                            raise EnvironmentError(
                                "Provider state has an incompatible provider type.",
                                code="state_invalid",
                            )
                        raw = dump_json_bytes(state.model_dump(mode="json"), sort_keys=True)
                        if len(raw) > budget:
                            raise EnvironmentError(
                                "Provider state exceeds its encoded byte limit.",
                                code="state_too_large",
                            )
                        detached = EnvironmentBindingState.model_validate(json.loads(raw))
                        entries[entered.public.binding_id] = detached
                        remaining -= len(raw)
        except TimeoutError as exc:
            raise EnvironmentError("Environment state export timed out.", code="state_timeout") from exc
        except EnvironmentError:
            raise
        except (TypeError, ValueError) as exc:
            raise EnvironmentError("Provider state is not canonical JSON.", code="state_invalid") from exc
        result = EnvironmentState(
            observed_topology_version=topology.topology_version,
            bindings=entries,
        )
        if (
            len(dump_json_bytes(result.model_dump(mode="json"), sort_keys=True))
            > self._state_limits.max_aggregate_encoded_bytes
        ):
            raise EnvironmentError("Environment state exceeds aggregate limit.", code="state_too_large")
        return result

    async def restore_state(self, state: EnvironmentState) -> None:
        self._assert_open()
        if self._activation_state != "not_started":
            raise EnvironmentError(
                "Environment state must be restored before activation begins.",
                code="state_invalid",
            )
        if self._restored_state_topology_version is not None:
            raise EnvironmentError("Environment state was already restored.", code="state_invalid")
        try:
            raw_state = dump_json_bytes(state.model_dump(mode="json"), sort_keys=True)
            detached = EnvironmentState.model_validate(json.loads(raw_state))
        except (TypeError, ValueError) as exc:
            raise EnvironmentError("Environment state is not canonical JSON.", code="state_invalid") from exc
        if len(detached.bindings) > self._state_limits.max_binding_entries:
            raise EnvironmentError("Environment state has too many entries.", code="state_too_large")
        if len(raw_state) > self._state_limits.max_aggregate_encoded_bytes:
            raise EnvironmentError("Environment state exceeds aggregate limit.", code="state_too_large")
        for entry in detached.bindings.values():
            encoded = dump_json_bytes(entry.model_dump(mode="json"), sort_keys=True)
            if len(encoded) > self._state_limits.max_binding_encoded_bytes:
                raise EnvironmentError("Environment state entry exceeds its limit.", code="state_too_large")
        matched: list[tuple[str, EnvironmentBindingState, _EnteredBinding]] = []
        for binding_id, entry in detached.bindings.items():
            entered = self._entered.get(binding_id)
            if entered is None:
                continue
            if entry.provider_type != entered.public.provider_type:
                raise EnvironmentError(
                    "Environment state provider type is incompatible.",
                    code="state_invalid",
                    details={"binding_id": binding_id},
                )
            matched.append((binding_id, entry, entered))
        try:
            async with asyncio.timeout(self._state_limits.restore_timeout_seconds):
                for _, entry, entered in matched:
                    async with self._operation_lease(
                        entered,
                        EnvironmentAction.STATE_RESTORE,
                        "state",
                        timeout_seconds=self._state_limits.restore_timeout_seconds,
                        timeout_code="state_timeout",
                    ):
                        await entered.provider.restore_state(entry)
        except TimeoutError as exc:
            raise EnvironmentError("Environment state restore timed out.", code="state_timeout") from exc
        self._restored_state_topology_version = detached.observed_topology_version


class NoopBoundEnvironment(CompositeBoundEnvironment):
    """The complete aggregate facade with an empty initial topology."""


@dataclass(slots=True)
class CompositeEnvironmentRunBinding(EnvironmentRunBinding):
    """Public single-use aggregate constructed from one complete topology request."""

    _initial_topology: EnvironmentTopologyRequest
    _topology_limits: EnvironmentTopologyLimits
    _state_limits: EnvironmentStateLimits
    _extensions: tuple[tuple[str, EnvironmentRunExtension], ...] = ()
    _noop: bool = False
    _controller: DynamicTopologyController = field(default_factory=DynamicTopologyController, init=False)
    _used: bool = field(default=False, init=False, repr=False)

    @property
    def controller(self) -> EnvironmentTopologyController:
        return self._controller

    @property
    def topology_limits(self) -> EnvironmentTopologyLimits:
        return self._topology_limits

    @property
    def state_limits(self) -> EnvironmentStateLimits:
        return self._state_limits

    @asynccontextmanager
    async def bind(
        self,
        *,
        run_id: str,
        instance: AgentInstanceContext,
    ) -> AsyncGenerator[BoundEnvironment]:
        if not isinstance(run_id, str) or not run_id.strip() or not isinstance(instance, AgentInstanceContext):
            raise EnvironmentError("Environment binding identity is invalid.", code="environment_request_invalid")
        if self._used:
            raise EnvironmentError(
                "EnvironmentRunBinding instances are single-use.",
                code="environment_binding_reused",
            )
        self._used = True
        claimed, reused = _claim_candidate_transfers(self._initial_topology.bindings)
        if reused:
            primary = EnvironmentError(
                "Environment provider binding was already transferred.",
                code="environment_binding_reused",
            )
            try:
                await _await_cleanup_shielded(_discard_candidate_instances(claimed))
            except BaseException as cleanup:
                raise BaseExceptionGroup(
                    "Environment initial candidate reuse rejection and cleanup failed",
                    [primary, cleanup],
                ) from None
            raise primary

        scopes: list[AbstractAsyncContextManager[BoundEnvironmentProvider]] = []
        entered: dict[str, _EnteredBinding] = {}
        successfully_entered: set[int] = set()
        try:
            for requested in self._initial_topology.bindings:
                candidate = requested.provider_binding
                if not isinstance(candidate, EnvironmentProviderBinding):
                    raise EnvironmentError(
                        "Every initial binding requires an EnvironmentProviderBinding.",
                        code="environment_request_invalid",
                        details={"binding_id": requested.binding_id},
                    )
                scope = candidate.bind(
                    run_id=run_id,
                    instance=instance,
                    binding_id=requested.binding_id,
                    binding_version=requested.binding_version,
                )
                async with asyncio.timeout(DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS):
                    provider = await scope.__aenter__()
                scopes.append(scope)
                successfully_entered.add(id(candidate))
                entered[requested.binding_id] = _validate_entered(requested, candidate, provider)

            topology = EnvironmentTopology(
                topology_version=self._initial_topology.topology_version,
                bindings=tuple(item.public for item in entered.values()),
                default_binding_id=self._initial_topology.default_binding_id,
            )
            observer = DynamicTopologyObserver(topology.topology_version)
            owned_scopes = {
                CompositeBoundEnvironment._binding_version_key(item): _OwnedProviderScope(
                    entered=item,
                    scope=scope,
                )
                for item, scope in zip(entered.values(), scopes, strict=True)
            }
            bound_type = NoopBoundEnvironment if self._noop else CompositeBoundEnvironment
            bound = bound_type(
                run_id=run_id,
                instance=instance,
                initial_request=self._initial_topology,
                topology=topology,
                entered=entered,
                owned_scopes=owned_scopes,
                topology_limits=self._topology_limits,
                state_limits=self._state_limits,
                observer=observer,
                controller=self._controller,
                extensions=self._extensions,
            )
            self._controller.attach(bound)
            scopes.clear()
            try:
                yield bound
            finally:
                active_error = sys.exception()
                try:
                    await _await_cleanup_shielded(
                        _teardown_entered_environment(
                            controller=self._controller,
                            bound=bound,
                            observer=observer,
                        )
                    )
                except BaseException as cleanup_error:
                    if active_error is not None and active_error is not cleanup_error:
                        raise BaseExceptionGroup(
                            "Environment operation and aggregate cleanup failed",
                            [active_error, cleanup_error],
                        ) from None
                    raise
        except BaseException as primary:
            if not self._controller.is_closed:
                await self._controller.close()
            try:
                await _discard_candidates(self._initial_topology.bindings, successfully_entered)
            except BaseException as cleanup:
                raise BaseExceptionGroup(
                    "Environment entry and candidate cleanup failed",
                    [primary, cleanup],
                ) from None
            raise
        finally:
            active_error = sys.exception()
            try:
                await _await_cleanup_shielded(_close_provider_scopes(scopes))
            except BaseException as cleanup_error:
                if active_error is not None and active_error is not cleanup_error:
                    raise BaseExceptionGroup(
                        "Environment operation and provider scope cleanup failed",
                        [active_error, cleanup_error],
                    ) from None
                raise


class NoopEnvironmentRunBinding(CompositeEnvironmentRunBinding):
    """Public empty aggregate; its stable facade can support later topology updates."""

    def __init__(
        self,
        *,
        topology_version: int = 1,
        topology_limits: EnvironmentTopologyLimits | None = None,
        state_limits: EnvironmentStateLimits | None = None,
    ) -> None:
        limits = topology_limits or EnvironmentTopologyLimits()
        state = state_limits or EnvironmentStateLimits()
        request = EnvironmentTopologyRequest(
            topology_version=topology_version,
            bindings=(),
            default_binding_id=None,
        )
        super().__init__(
            _initial_topology=request,
            _topology_limits=limits.model_copy(deep=True),
            _state_limits=state.model_copy(deep=True),
            _extensions=(),
            _noop=True,
        )


def _validate_request(request: EnvironmentTopologyRequest, limits: EnvironmentTopologyLimits) -> None:
    if len(request.bindings) > limits.max_bindings:
        raise EnvironmentError("Environment topology exceeds max_bindings.", code="environment_topology_invalid")
    ids = [item.binding_id for item in request.bindings]
    aliases = [item.alias for item in request.bindings]
    candidate_ids = [id(item.provider_binding) for item in request.bindings if item.provider_binding is not None]
    if len(ids) != len(set(ids)) or len(aliases) != len(set(aliases)):
        raise EnvironmentError(
            "Environment binding IDs and aliases must be unique.", code="environment_topology_invalid"
        )
    if len(candidate_ids) != len(set(candidate_ids)):
        raise EnvironmentError(
            "One provider candidate cannot occupy multiple bindings.",
            code="environment_topology_invalid",
        )
    if request.default_binding_id is not None and request.default_binding_id not in ids:
        raise EnvironmentError(
            "default_binding_id is not present in the topology.", code="environment_topology_invalid"
        )
    if any(
        item.default_working_directory is not None
        and (
            not item.default_working_directory.startswith("/")
            or any(segment in {".", ".."} for segment in item.default_working_directory.split("/"))
        )
        for item in request.bindings
    ):
        raise EnvironmentError(
            "Environment default working directories must be canonical absolute paths.",
            code="environment_topology_invalid",
        )


def _validate_entered(
    requested: EnvironmentBindingRequest,
    candidate: EnvironmentProviderBinding,
    provider: BoundEnvironmentProvider,
) -> _EnteredBinding:
    descriptor = provider.descriptor
    availability = provider.availability
    operations = provider.operations
    if not isinstance(descriptor, EnvironmentDescriptor):
        raise EnvironmentError("Provider returned an invalid descriptor.", code="environment_provider_failure")
    if not isinstance(availability, EnvironmentAvailability):
        raise EnvironmentError("Provider returned invalid availability.", code="environment_provider_failure")
    if not isinstance(operations, EnvironmentProviderOperations):
        raise EnvironmentError("Provider returned invalid operations.", code="environment_provider_failure")
    if provider.provider_type != candidate.provider_type or provider.environment_id != candidate.environment_id:
        raise EnvironmentError("Provider identity changed during entry.", code="environment_provider_failure")
    if not availability.ready_families <= descriptor.operation_families:
        raise EnvironmentError("Provider readiness advertises an absent family.", code="environment_provider_failure")
    if not callable(getattr(provider, "ensure_ready", None)):
        raise EnvironmentError("Provider has no readiness path.", code="environment_provider_failure")
    if "state" in descriptor.operation_families and (
        not callable(getattr(provider, "export_state", None)) or not callable(getattr(provider, "restore_state", None))
    ):
        raise EnvironmentError("Provider has no complete state codec path.", code="environment_provider_failure")

    facet_families = {
        family
        for family in ("files", "shell", "processes", "ports", "outputs")
        if getattr(operations, family) is not None
    }
    advertised_facets = set(descriptor.operation_families) - {"state"}
    if facet_families != advertised_facets:
        raise EnvironmentError(
            "Provider descriptor and operation facets disagree.",
            code="environment_provider_failure",
            details={"binding_id": requested.binding_id},
        )
    for action in descriptor.permissions.operations:
        dispatch = ENVIRONMENT_ACTION_DISPATCH[action]
        if dispatch.family == "state":
            method = getattr(provider, dispatch.method, None)
        else:
            method = getattr(getattr(operations, dispatch.facet), dispatch.method, None)
        if not callable(method):
            raise EnvironmentError(
                "Provider permission has no executable semantic method.",
                code="environment_provider_failure",
                details={"action": action.value},
            )

    effective = EnvironmentPermissionSet(
        operations=requested.permission_ceiling.operations & descriptor.permissions.operations
    )
    public = EnvironmentBinding(
        binding_id=requested.binding_id,
        binding_version=requested.binding_version,
        alias=requested.alias,
        provider_type=provider.provider_type,
        descriptor=descriptor,
        permission_ceiling=effective,
        default_working_directory=requested.default_working_directory,
    )
    return _EnteredBinding(
        public=public,
        provider=provider,
        operations=operations,
        environment_id=provider.environment_id,
    )


_SUPERVISED_CLEANUP_TASKS: set[asyncio.Task[Any]] = set()


def _consume_background_task(task: asyncio.Task[Any]) -> None:
    _SUPERVISED_CLEANUP_TASKS.discard(task)
    if not task.cancelled():
        task.exception()


def _supervise_cleanup_task(task: asyncio.Task[Any]) -> None:
    _SUPERVISED_CLEANUP_TASKS.add(task)
    task.add_done_callback(_consume_background_task)


async def _await_cleanup_shielded(operation: Coroutine[Any, Any, None]) -> None:
    """Finish one owned teardown worker before propagating any caller cancellation."""
    cleanup = asyncio.create_task(operation)
    cancellations: list[asyncio.CancelledError] = []
    current = asyncio.current_task()
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError as exc:
            cancellations.append(exc)
            if current is not None:
                current.uncancel()
    cleanup_error: BaseException | None = None
    try:
        cleanup.result()
    except BaseException as exc:
        cleanup_error = exc
    if cancellations:
        if cleanup_error is not None:
            raise BaseExceptionGroup(
                "Environment cancellation and cleanup failed",
                [cancellations[0], cleanup_error],
            ) from None
        raise cancellations[0]
    if cleanup_error is not None:
        raise cleanup_error


async def _teardown_entered_environment(
    *,
    controller: DynamicTopologyController,
    bound: CompositeBoundEnvironment,
    observer: DynamicTopologyObserver,
) -> None:
    failures: list[BaseException] = []
    controller.begin_close()
    try:
        await bound._close()
    except BaseException as exc:
        failures.append(exc)
    try:
        await observer.close()
    except BaseException as exc:
        failures.append(exc)
    try:
        await controller.close()
    except BaseException as exc:
        failures.append(exc)
    if failures:
        raise BaseExceptionGroup("Environment entered-resource cleanup failed", failures)


async def _close_extension_scopes(
    scopes: tuple[tuple[str, AbstractAsyncContextManager[None]], ...],
) -> None:
    failures: list[BaseException] = []
    for extension_id, scope in reversed(scopes):
        try:
            await scope.__aexit__(None, None, None)
        except BaseException:
            failures.append(
                EnvironmentError(
                    "Environment run extension cleanup failed.",
                    code="environment_extension_cleanup_failed",
                    details={"extension_id": extension_id},
                )
            )
    if failures:
        raise BaseExceptionGroup("Environment run extension cleanup failed", failures)


async def _close_provider_scopes_after_tasks(
    tasks: tuple[asyncio.Task[Any], ...],
    scopes: list[AbstractAsyncContextManager[BoundEnvironmentProvider]],
) -> None:
    await asyncio.gather(*tasks, return_exceptions=True)
    await _close_provider_scopes(scopes)


async def _close_provider_scopes(
    scopes: list[AbstractAsyncContextManager[BoundEnvironmentProvider]],
) -> None:
    failures: list[BaseException] = []
    for scope in reversed(scopes):
        task = asyncio.create_task(scope.__aexit__(None, None, None))
        done, _ = await asyncio.wait(
            (task,),
            timeout=DEFAULT_ENVIRONMENT_CLEANUP_TIMEOUT_SECONDS,
        )
        if task not in done:
            task.cancel()
            _supervise_cleanup_task(task)
            failures.append(
                EnvironmentError(
                    "Environment provider scope cleanup timed out.",
                    code="environment_cleanup_timeout",
                )
            )
            continue
        try:
            task.result()
        except BaseException as exc:
            failures.append(exc)
    if failures:
        raise BaseExceptionGroup("Environment provider scope cleanup failed", failures)


def _claim_candidate_transfers(
    requests: tuple[EnvironmentBindingRequest, ...],
) -> tuple[tuple[EnvironmentProviderBinding, ...], tuple[EnvironmentProviderBinding, ...]]:
    candidates = {
        id(item.provider_binding): item.provider_binding
        for item in requests
        if isinstance(item, EnvironmentBindingRequest) and isinstance(item.provider_binding, EnvironmentProviderBinding)
    }
    claimed: list[EnvironmentProviderBinding] = []
    reused: list[EnvironmentProviderBinding] = []
    for candidate in candidates.values():
        transferred = EnvironmentProviderBinding._claim_transfer(candidate)
        (claimed if transferred else reused).append(candidate)
    return tuple(claimed), tuple(reused)


async def _discard_candidate_instances(candidates: tuple[EnvironmentProviderBinding, ...]) -> None:
    failures: list[BaseException] = []
    for candidate in candidates:
        task = asyncio.create_task(candidate.discard())
        done, _ = await asyncio.wait(
            (task,),
            timeout=DEFAULT_ENVIRONMENT_CLEANUP_TIMEOUT_SECONDS,
        )
        if task not in done:
            task.cancel()
            _supervise_cleanup_task(task)
            failures.append(
                EnvironmentError(
                    "Environment candidate discard timed out.",
                    code="environment_cleanup_timeout",
                )
            )
            continue
        try:
            task.result()
        except BaseException as exc:
            failures.append(exc)
    if failures:
        raise BaseExceptionGroup("Environment candidate discard failed", failures)


async def _discard_candidates(
    requests: tuple[EnvironmentBindingRequest, ...],
    successfully_entered: set[int],
) -> None:
    candidates = {
        id(item.provider_binding): item.provider_binding
        for item in requests
        if isinstance(item, EnvironmentBindingRequest)
        and isinstance(item.provider_binding, EnvironmentProviderBinding)
        and id(item.provider_binding) not in successfully_entered
    }
    await _discard_candidate_instances(tuple(candidates.values()))


async def _cleanup_topology_candidates(
    *,
    requests: tuple[EnvironmentBindingRequest, ...],
    prepared_scopes: Mapping[_BindingVersionKey, _OwnedProviderScope],
    successfully_entered: set[int],
) -> None:
    failures: list[BaseException] = []
    scopes = [owned.scope for owned in prepared_scopes.values()]
    try:
        await _close_provider_scopes(scopes)
    except BaseException as exc:
        failures.append(exc)
    try:
        await _discard_candidates(requests, successfully_entered)
    except BaseException as exc:
        failures.append(exc)
    if failures:
        raise BaseExceptionGroup("Environment topology candidate cleanup failed", failures)


def _normalize_topology_request(request: object) -> EnvironmentTopologyRequest:
    if not isinstance(request, EnvironmentTopologyRequest):
        raise EnvironmentError("Environment topology request is invalid.", code="environment_request_invalid")
    bindings: list[EnvironmentBindingRequest] = []
    try:
        for item in request.bindings:
            if not isinstance(item, EnvironmentBindingRequest):
                raise TypeError("invalid binding request")
            if not isinstance(item.permission_ceiling, EnvironmentPermissionSet):
                raise TypeError("invalid permission ceiling")
            bindings.append(
                EnvironmentBindingRequest(
                    binding_id=item.binding_id,
                    binding_version=item.binding_version,
                    alias=item.alias,
                    permission_ceiling=EnvironmentPermissionSet(
                        operations=frozenset(item.permission_ceiling.operations)
                    ),
                    default_working_directory=item.default_working_directory,
                    provider_binding=item.provider_binding,
                )
            )
        return EnvironmentTopologyRequest(
            topology_version=request.topology_version,
            bindings=tuple(bindings),
            default_binding_id=request.default_binding_id,
        )
    except (TypeError, ValueError) as exc:
        raise EnvironmentError(
            "Environment topology request is invalid.",
            code="environment_request_invalid",
        ) from exc


def _topology_request_digest(request: EnvironmentTopologyRequest) -> str:
    payload = {
        "topology_version": request.topology_version,
        "bindings": [
            {
                "binding_id": item.binding_id,
                "binding_version": item.binding_version,
                "alias": item.alias,
                "permission_ceiling": sorted(action.value for action in item.permission_ceiling.operations),
                "default_working_directory": item.default_working_directory,
            }
            for item in request.bindings
        ],
        "default_binding_id": request.default_binding_id,
    }
    return sha256(dump_json_bytes(payload, sort_keys=True)).hexdigest()


def _topology_change(
    previous: EnvironmentTopology,
    current: EnvironmentTopology,
    digest: str,
) -> EnvironmentTopologyChange:
    before = {item.binding_id: item for item in previous.bindings}
    after = {item.binding_id: item for item in current.bindings}
    changes: list[EnvironmentTopologyBindingChange] = []
    for item in previous.bindings:
        replacement = after.get(item.binding_id)
        if replacement is None:
            changes.append(
                EnvironmentTopologyBindingChange(
                    kind="removed",
                    binding_id=item.binding_id,
                    previous_version=item.binding_version,
                    current_version=None,
                    previous_alias=item.alias,
                    current_alias=None,
                )
            )
        elif replacement.binding_version != item.binding_version:
            changes.append(
                EnvironmentTopologyBindingChange(
                    kind="refreshed",
                    binding_id=item.binding_id,
                    previous_version=item.binding_version,
                    current_version=replacement.binding_version,
                    previous_alias=item.alias,
                    current_alias=replacement.alias,
                )
            )
    for item in current.bindings:
        if item.binding_id not in before:
            changes.append(
                EnvironmentTopologyBindingChange(
                    kind="added",
                    binding_id=item.binding_id,
                    previous_version=None,
                    current_version=item.binding_version,
                    previous_alias=None,
                    current_alias=item.alias,
                )
            )
    return EnvironmentTopologyChange(
        previous_version=previous.topology_version,
        current_version=current.topology_version,
        request_digest=digest,
        bindings=tuple(changes),
    )


def _normalize_environment_run_extensions(
    extensions: Sequence[EnvironmentRunExtension],
) -> tuple[tuple[str, EnvironmentRunExtension], ...]:
    try:
        supplied = tuple(extensions)
    except TypeError:
        raise EnvironmentError(
            "Environment run extensions must be a finite sequence.",
            code="environment_extension_id_invalid",
        ) from None
    seen: set[str] = set()
    captured: list[tuple[str, EnvironmentRunExtension]] = []
    for extension in supplied:
        if not isinstance(extension, EnvironmentRunExtension):
            raise EnvironmentError(
                "Environment run extensions must implement EnvironmentRunExtension.",
                code="environment_extension_id_invalid",
            )
        try:
            extension_id = extension.extension_id
        except Exception:
            raise EnvironmentError(
                "Environment run extension ID could not be read.",
                code="environment_extension_id_invalid",
            ) from None
        if (
            not isinstance(extension_id, str)
            or not extension_id
            or extension_id != extension_id.strip()
            or len(extension_id) > _MAX_ENVIRONMENT_EXTENSION_ID_LENGTH
        ):
            raise EnvironmentError(
                "Environment run extension IDs must be bounded non-blank strings without surrounding whitespace.",
                code="environment_extension_id_invalid",
            )
        if extension_id in seen:
            raise EnvironmentError(
                "Environment run extension IDs must be unique.",
                code="environment_extension_duplicate",
                details={"extension_id": extension_id},
            )
        seen.add(extension_id)
        captured.append((extension_id, extension))
    return tuple(captured)


def create_environment_run_binding(
    *,
    initial_topology: EnvironmentTopologyRequest,
    topology_limits: EnvironmentTopologyLimits,
    state_limits: EnvironmentStateLimits,
    extensions: Sequence[EnvironmentRunExtension] = (),
) -> EnvironmentRunBinding:
    """Capture one initial complete topology in a fresh single-use aggregate."""
    captured = _normalize_topology_request(initial_topology)
    captured_extensions = _normalize_environment_run_extensions(extensions)
    _validate_request(captured, topology_limits)
    return CompositeEnvironmentRunBinding(
        _initial_topology=captured,
        _topology_limits=topology_limits.model_copy(deep=True),
        _state_limits=state_limits.model_copy(deep=True),
        _extensions=captured_extensions,
    )


def create_noop_environment_run_binding(
    *,
    topology_version: int = 1,
    topology_limits: EnvironmentTopologyLimits | None = None,
    state_limits: EnvironmentStateLimits | None = None,
) -> EnvironmentRunBinding:
    """Create the finite empty aggregate used by embedded local runs."""
    limits = topology_limits or EnvironmentTopologyLimits()
    state = state_limits or EnvironmentStateLimits()
    request = EnvironmentTopologyRequest(
        topology_version=topology_version,
        bindings=(),
        default_binding_id=None,
    )
    _validate_request(request, limits)
    return NoopEnvironmentRunBinding(
        topology_version=topology_version,
        topology_limits=limits,
        state_limits=state,
    )
