from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from a13n_envd_client import EIPSession
from a13n_envd_client.eip import v1 as eip
from a13n_environment_provider import EIPSessionSource

from ..models import (
    EnvironmentAction,
    EnvironmentAvailability,
    EnvironmentBindingState,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentMountDescriptor,
    EnvironmentOperationFamily,
    EnvironmentPermissionSet,
)
from ..providers import BoundEnvironmentProvider, EnvironmentProviderBinding, EnvironmentProviderOperations
from ._common import invoke
from .files import EIPFileOperator
from .output import EIPOutputOperations, EIPOutputRegistry
from .processes import (
    EIPPortOperations,
    EIPProcessOperations,
    EIPShellOperations,
    _ProcessConversions,
)

_METHOD_ACTIONS: dict[str, tuple[EnvironmentAction, ...]] = {
    "file.stat": (EnvironmentAction.FILE_STAT,),
    "file.read_text": (EnvironmentAction.FILE_READ_TEXT,),
    "file.open_reader": (EnvironmentAction.FILE_READ_BYTES,),
    "file.write_text": (EnvironmentAction.FILE_WRITE_TEXT,),
    "file.patch_text": (EnvironmentAction.FILE_PATCH_TEXT,),
    "file.list": (EnvironmentAction.FILE_LIST,),
    "file.find": (EnvironmentAction.FILE_QUERY,),
    "file.search": (EnvironmentAction.FILE_SEARCH_TEXT,),
    "file.mkdir": (EnvironmentAction.FILE_MKDIR,),
    "file.move": (EnvironmentAction.FILE_MOVE,),
    "file.remove": (EnvironmentAction.FILE_REMOVE,),
    "file.open_writer": (EnvironmentAction.FILE_WRITE_BYTES,),
    "file.copy": (EnvironmentAction.FILE_COPY_SOURCE, EnvironmentAction.FILE_COPY_DESTINATION),
    "shell.exec": (EnvironmentAction.SHELL_EXEC,),
    "process.start": (EnvironmentAction.PROCESS_START,),
    "process.inspect": (EnvironmentAction.PROCESS_INSPECT,),
    "process.write_stdin": (EnvironmentAction.PROCESS_WRITE_STDIN,),
    "process.close_stdin": (EnvironmentAction.PROCESS_CLOSE_STDIN,),
    "process.signal": (EnvironmentAction.PROCESS_SIGNAL,),
    "process.wait": (EnvironmentAction.PROCESS_WAIT,),
    "process.kill": (EnvironmentAction.PROCESS_KILL,),
    "process.release": (EnvironmentAction.PROCESS_RELEASE,),
    "output.read": (EnvironmentAction.OUTPUT_READ,),
    "output.release": (EnvironmentAction.OUTPUT_RELEASE,),
    "port.inspect": (EnvironmentAction.PORT_INSPECT,),
    "port.wait": (EnvironmentAction.PORT_WAIT,),
}


class EIPEnvironmentProviderBinding(EnvironmentProviderBinding):
    def __init__(
        self,
        *,
        environment_id: str,
        session_source: EIPSessionSource,
    ) -> None:
        if not environment_id:
            raise ValueError("environment_id must not be empty")
        self._environment_id = environment_id
        self._session_source = session_source
        self._scope_created = False
        self._entry_started = False
        self._discarded = False

    @property
    def provider_type(self) -> str:
        return "a13n.eip"

    @property
    def environment_id(self) -> str:
        return self._environment_id

    def bind(
        self,
        *,
        run_id: str,
        instance,
        binding_id: str,
        binding_version: int,
    ) -> AbstractAsyncContextManager[BoundEnvironmentProvider]:
        del run_id, instance
        if self._discarded or self._scope_created:
            raise EnvironmentError("EIP provider binding was already consumed", code="environment_conflict")
        self._scope_created = True
        return self._bind(binding_id=binding_id, binding_version=binding_version)

    @asynccontextmanager
    async def _bind(
        self,
        *,
        binding_id: str,
        binding_version: int,
    ) -> AsyncGenerator[BoundEnvironmentProvider]:
        self._entry_started = True
        async with self._session_source.open_session(
            expected_environment_id=self._environment_id,
            required_methods=frozenset({"environment.describe", "environment.readiness", "session.close"}),
        ) as session:
            if session.descriptor.environment_id != self._environment_id:
                raise EnvironmentError(
                    "EIP session returned a different environment identity",
                    code="environment_stale_binding",
                )
            provider = _BoundEIPProvider(
                session=session,
                environment_id=self._environment_id,
                binding_id=binding_id,
                binding_version=binding_version,
            )
            try:
                yield provider
            finally:
                await provider.close()

    async def discard(self) -> None:
        if self._discarded or self._entry_started:
            return
        await self._session_source.discard()
        self._discarded = True


class _BoundEIPProvider:
    def __init__(
        self,
        *,
        session: EIPSession,
        environment_id: str,
        binding_id: str,
        binding_version: int,
    ) -> None:
        self._session = session
        self._environment_id = environment_id
        self._binding_id = binding_id
        self._binding_version = binding_version
        self._generation = str(session.descriptor.generation)
        self._descriptor = _convert_descriptor(session.descriptor)
        methods = set(session.descriptor.available_methods)
        files = EIPFileOperator(
            session=session,
            environment_id=environment_id,
            binding_id=binding_id,
            binding_version=binding_version,
            generation=self._generation,
        )
        outputs = EIPOutputRegistry(
            session=session,
            environment_id=environment_id,
            binding_id=binding_id,
            binding_version=binding_version,
            generation=self._generation,
        )
        self._outputs = outputs
        conversions = _ProcessConversions(
            session=session,
            files=files,
            outputs=outputs,
            provider_type=self.provider_type,
            environment_id=environment_id,
            binding_id=binding_id,
            binding_version=binding_version,
            generation=self._generation,
        )
        processes = EIPProcessOperations(conversions) if "process.start" in methods else None
        self._conversions = conversions
        self._operations = EnvironmentProviderOperations(
            files=files if any(method.startswith("file.") for method in methods) else None,
            shell=EIPShellOperations(conversions) if "shell.exec" in methods else None,
            processes=processes,
            ports=EIPPortOperations(conversions) if {"port.inspect", "port.wait"} & methods else None,
            outputs=EIPOutputOperations(outputs) if "output.read" in methods else None,
        )
        self._availability = EnvironmentAvailability(
            status="available",
            ready_families=self._descriptor.operation_families,
        )

    @property
    def provider_type(self) -> str:
        return "a13n.eip"

    @property
    def environment_id(self) -> str:
        return self._environment_id

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self._descriptor

    @property
    def availability(self) -> EnvironmentAvailability:
        return self._availability

    @property
    def operations(self) -> EnvironmentProviderOperations:
        return self._operations

    async def ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        missing = operations - self._descriptor.operation_families
        if missing:
            raise EnvironmentError(
                "EIP provider does not expose the requested operation family",
                code="environment_unsupported",
            )
        readiness = await invoke(self._session.readiness())
        if not readiness.ready:
            self._availability = EnvironmentAvailability(status="unavailable")
            raise EnvironmentError(
                "EIP environment is not ready",
                code="environment_unavailable",
                retry_hint="new_run",
            )

    async def close(self) -> None:
        first_error: EnvironmentError | None = None
        try:
            await self._conversions.cleanup_owned()
        except EnvironmentError as error:
            first_error = error
        try:
            await self._outputs.cleanup_pending()
        except EnvironmentError as error:
            if first_error is None:
                first_error = error
        if first_error is not None:
            raise first_error

    async def export_state(self, *, max_bytes: int) -> EnvironmentBindingState | None:
        del max_bytes
        return None

    async def restore_state(self, state: EnvironmentBindingState) -> None:
        del state
        raise EnvironmentError("EIP provider state restore is unsupported", code="environment_unsupported")


def _convert_descriptor(descriptor: eip.EnvironmentDescriptor) -> EnvironmentDescriptor:
    methods = set(descriptor.available_methods)
    actions = {action for method in methods for action in _METHOD_ACTIONS.get(method, ())}
    if "process.inspect" in methods and "output.read" in methods:
        actions.add(EnvironmentAction.PROCESS_READ_OUTPUT)
    families: set[EnvironmentOperationFamily] = set()
    if any(method.startswith("file.") for method in methods):
        families.add("files")
    if "shell.exec" in methods:
        families.add("shell")
    if any(method.startswith("process.") for method in methods):
        families.add("processes")
    if any(method.startswith("port.") for method in methods):
        families.add("ports")
    if any(method.startswith("output.") for method in methods):
        families.add("outputs")
    limits = descriptor.limits
    return EnvironmentDescriptor(
        generation=str(descriptor.generation),
        operation_families=frozenset(families),
        permissions=EnvironmentPermissionSet(operations=frozenset(actions)),
        limits={
            "max_request_bytes": limits.max_request_bytes,
            "max_response_bytes": limits.max_response_bytes,
            "max_concurrent_operations": limits.max_concurrent_operations,
            "max_processes": limits.max_processes,
            "max_operation_duration_ms": limits.max_operation_duration_ms,
            "max_output_preview_bytes": limits.max_output_preview_bytes,
            "max_output_bytes_per_stream": limits.max_output_bytes_per_stream,
            "max_transfer_frame_bytes": limits.max_transfer_frame_bytes,
            "max_concurrent_file_transfers": limits.max_concurrent_file_transfers,
            "max_file_transfer_bytes": limits.max_file_transfer_bytes,
        },
        mounts=tuple(
            EnvironmentMountDescriptor(
                name=mount.mount_id,
                path=mount.logical_root,
                read_only=not mount.writable,
            )
            for mount in descriptor.mounts
        ),
    )
