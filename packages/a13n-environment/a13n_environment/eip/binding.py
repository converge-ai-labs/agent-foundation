from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from a13n_envd_client import EIPSession
from a13n_envd_client.eip import v1 as eip

from ..attachments import EIPSessionSource
from ..envd_policy import EnvdBoundaryRequirement
from ..models import (
    EnvironmentAction,
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentOperationFamily,
    EnvironmentPermissionSet,
)
from ..operations import EnvironmentOperations
from ._common import invoke
from .computer import EIPComputerOperations
from .files import EIPFileOperator
from .output import EIPOutputOperations, EIPOutputRegistry
from .processes import EIPPortOperations, EIPProcessOperations, EIPShellOperations, _ProcessConversions

_METHOD_ACTIONS: dict[str, tuple[EnvironmentAction, ...]] = {
    "computer.describe": (EnvironmentAction.COMPUTER_DESCRIBE,),
    "computer.observe": (EnvironmentAction.COMPUTER_OBSERVE,),
    "computer.click": (EnvironmentAction.COMPUTER_CLICK,),
    "computer.move": (EnvironmentAction.COMPUTER_MOVE,),
    "computer.drag": (EnvironmentAction.COMPUTER_DRAG,),
    "computer.scroll": (EnvironmentAction.COMPUTER_SCROLL,),
    "computer.type_text": (EnvironmentAction.COMPUTER_TYPE_TEXT,),
    "computer.press_keys": (EnvironmentAction.COMPUTER_PRESS_KEYS,),
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


@asynccontextmanager
async def open_eip_environment(
    *,
    provider_key: str,
    environment_id: str,
    session_source: EIPSessionSource,
    execution_id: str,
    device_id: str,
    working_directory: str | None = None,
    required_methods: frozenset[str] = frozenset(),
    egress: eip.EgressPolicy | None = None,
    expected_boundary: EnvdBoundaryRequirement | None = None,
) -> AsyncGenerator[EIPEnvironmentSession]:
    """Open one provider-owned EIP session and expose semantic operation facets."""
    async with session_source.open_session(
        expected_device_id=device_id,
        egress=egress,
        expected_boundary=expected_boundary,
        working_directory=working_directory,
        required_methods=required_methods
        | frozenset({"environment.describe", "environment.readiness", "session.close"}),
    ) as session:
        if session.descriptor.device_id != device_id:
            raise EnvironmentError(
                "EIP Session returned a different Device identity",
                code="environment_stale_mount",
            )
        environment = EIPEnvironmentSession(
            session=session,
            provider_key=provider_key,
            environment_id=environment_id,
            execution_id=execution_id,
        )
        try:
            yield environment
        finally:
            await environment.close()


class EIPEnvironmentSession:
    """Entered EIP operations owned by one concrete Environment adapter."""

    def __init__(
        self,
        *,
        session: EIPSession,
        provider_key: str,
        environment_id: str,
        execution_id: str,
    ) -> None:
        self._session = session
        self._provider_key = provider_key
        self._environment_id = environment_id
        self._descriptor = _convert_descriptor(session.descriptor)
        self._generation = self._descriptor.generation
        methods = set(session.descriptor.available_methods)
        files = EIPFileOperator(
            session=session,
            environment_id=environment_id,
            execution_id=execution_id,
            generation=self._generation,
        )
        outputs = EIPOutputRegistry(
            session=session,
            environment_id=environment_id,
            execution_id=execution_id,
            generation=self._generation,
        )
        self._outputs = outputs
        conversions = _ProcessConversions(
            session=session,
            files=files,
            outputs=outputs,
            provider_type=provider_key,
            environment_id=environment_id,
            execution_id=execution_id,
            generation=self._generation,
        )
        self._conversions = conversions
        self._operations = EnvironmentOperations(
            files=files if any(method.startswith("file.") for method in methods) else None,
            shell=EIPShellOperations(conversions) if "shell.exec" in methods else None,
            processes=EIPProcessOperations(conversions) if "process.start" in methods else None,
            ports=EIPPortOperations(conversions) if {"port.inspect", "port.wait"} & methods else None,
            outputs=EIPOutputOperations(outputs) if "output.read" in methods else None,
            computer=EIPComputerOperations(session, execution_id=execution_id, generation=self._generation)
            if any(method.startswith("computer.") for method in methods)
            else None,
        )
        self._availability = EnvironmentAvailability(
            status="available",
            ready_families=self._descriptor.operation_families,
        )

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self._descriptor

    @property
    def availability(self) -> EnvironmentAvailability:
        return self._availability

    @property
    def operations(self) -> EnvironmentOperations:
        return self._operations

    async def check_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        missing = operations - self._descriptor.operation_families
        if missing:
            raise EnvironmentError(
                "EIP provider does not expose the requested operation family",
                code="environment_unsupported",
            )
        try:
            readiness = await invoke(self._session.readiness())
        except EnvironmentError:
            self._availability = EnvironmentAvailability(status="unavailable")
            raise
        if not readiness.ready:
            self._availability = EnvironmentAvailability(status="unavailable")
            raise EnvironmentError(
                "EIP environment is not ready",
                code="environment_unavailable",
                retry_hint="new_run",
            )
        self._availability = EnvironmentAvailability(
            status="available", ready_families=self._descriptor.operation_families
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


def _convert_descriptor(descriptor: eip.SessionDescriptor) -> EnvironmentDescriptor:
    methods = set(descriptor.available_methods)
    actions = {action for method in methods for action in _METHOD_ACTIONS.get(method, ())}
    if "process.inspect" in methods and "output.read" in methods:
        actions.add(EnvironmentAction.PROCESS_READ_OUTPUT)
    if "computer.close_observation" not in methods:
        actions.discard(EnvironmentAction.COMPUTER_OBSERVE)
    families: set[EnvironmentOperationFamily] = set()
    if any(method.startswith("computer.") for method in methods):
        families.add("computer")
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
        generation=f"{descriptor.generation}:{descriptor.session_id}",
        execution_boundary=descriptor.boundary.model_dump(mode="json"),
        working_directory=descriptor.working_directory,
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
        mounts=(),
    )


def configured_descriptor(*, working_directory: str | None = None) -> EnvironmentDescriptor:
    actions = {action for values in _METHOD_ACTIONS.values() for action in values}
    actions.add(EnvironmentAction.PROCESS_READ_OUTPUT)
    from ..models import ENVIRONMENT_ACTION_DISPATCH

    return EnvironmentDescriptor(
        generation="unprepared",
        working_directory=working_directory or "/",
        operation_families=frozenset(ENVIRONMENT_ACTION_DISPATCH[action].family for action in actions),
        permissions=EnvironmentPermissionSet(operations=frozenset(actions)),
        limits={},
        mounts=(),
    )
