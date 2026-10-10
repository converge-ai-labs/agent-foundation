"""E2B execution implementation."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from .._backend import ExecutionBackend
from .._guest_files import GuestFiles
from .._guest_ports import GuestPorts
from ..errors import EnvironmentProviderErrorCategory as Category
from ..models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentOperationFamily,
    EnvironmentState,
)
from ..operations import EnvironmentOperations
from .commands import GuestCommands
from .configuration import (
    E2BEnvironmentConfiguration,
)
from .connection import close_sandbox, open_sandbox
from .errors import provider_error, sdk_errors
from .processes import E2BProcesses
from .shared import E2BProviderRuntime, E2BReference, descriptor

if TYPE_CHECKING:
    from e2b import AsyncSandbox


class E2BExecution(E2BReference, ExecutionBackend):
    def __init__(
        self,
        configuration: E2BEnvironmentConfiguration,
        *,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: E2BProviderRuntime,
    ) -> None:
        super().__init__(configuration, environment_id=environment_id, state=state, runtime=runtime)
        self._descriptor = descriptor(configuration)
        self._availability = EnvironmentAvailability(status="preparing")
        self._operations = EnvironmentOperations()
        self._commands: GuestCommands | None = None
        self._processes: E2BProcesses | None = None
        self._sandbox: AsyncSandbox | None = None

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self._descriptor

    @property
    def availability(self) -> EnvironmentAvailability:
        return self._availability

    @property
    def operations(self) -> EnvironmentOperations:
        return self._operations

    async def open(self, *, execution_id: str) -> None:
        if self._state is None:
            raise provider_error("provider_state_invalid", Category.INVALID)
        self._sandbox = await open_sandbox(self._state.sandbox_id, self._options(), self._validate_target)
        await self._open_operations(self._sandbox, execution_id)

    async def check_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        if operations - self.descriptor.operation_families:
            raise EnvironmentError("E2B operation family is unsupported.", code="environment_unsupported")
        if self._commands is None:
            raise EnvironmentError("E2B is unavailable.", code="environment_unavailable")
        with sdk_errors():
            ready = await self._commands.sandbox.is_running(request_timeout=self._configuration.request_timeout_seconds)
        if not ready:
            self._availability = EnvironmentAvailability(status="unavailable")
            raise EnvironmentError("E2B is unavailable.", code="environment_unavailable")

    async def close(self) -> None:
        try:
            if self._processes is not None:
                await self._processes.close()
        finally:
            if self._commands is not None:
                self._commands.closed = True
            self._commands = None
            self._processes = None
            self._operations = EnvironmentOperations()
            self._availability = EnvironmentAvailability(status="unavailable")
            sandbox, self._sandbox = self._sandbox, None
            if sandbox is not None:
                await close_sandbox(sandbox)

    async def _open_operations(self, sandbox: AsyncSandbox, execution_id: str) -> None:
        commands = GuestCommands(sandbox, self._configuration)
        commands.generation = "generation-" + hashlib.sha256(sandbox.sandbox_id.encode()).hexdigest()[:24]
        commands.execution_id = execution_id
        files = GuestFiles(commands)
        await files.stat("/")
        self._commands = commands
        processes = E2BProcesses(commands, self.environment_id)
        self._processes = processes
        self._operations = EnvironmentOperations(
            files=files,
            shell=processes,
            processes=processes,
            ports=GuestPorts(commands),
        )
        self._descriptor = descriptor(self._configuration, commands.generation, sandbox.sandbox_id)
        self._availability = EnvironmentAvailability(
            status="available", ready_families=self._descriptor.operation_families
        )
