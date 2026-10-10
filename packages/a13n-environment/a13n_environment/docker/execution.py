"""Docker execution implementation."""

from __future__ import annotations

import asyncio

from .._backend import ExecutionBackend
from .._guest_files import GuestFiles
from .._guest_ports import GuestPorts
from .._local_retention import LocalRetentionStore, create_retention_root
from ..errors import (
    EnvironmentProviderErrorCategory,
    provider_error,
)
from ..models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentOperationFamily,
    EnvironmentState,
)
from ..operations import EnvironmentOperations
from .commands import DockerCommands
from .configuration import DockerEnvironmentConfiguration
from .errors import engine_errors
from .processes import DockerProcesses
from .runtime import DockerProviderRuntime
from .shared import _KEY, DockerReference, _missing, descriptor


class DockerExecution(DockerReference, ExecutionBackend):
    def __init__(
        self,
        configuration: DockerEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: DockerProviderRuntime,
    ) -> None:
        super().__init__(configuration, environment_id, state, runtime)
        self._operations = EnvironmentOperations()
        self.commands: DockerCommands | None = None
        self.processes: DockerProcesses | None = None
        self.retention: LocalRetentionStore | None = None
        self._availability = EnvironmentAvailability(status="preparing")

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return descriptor(self.target.container_id if self.target else "unprepared", self.config)

    @property
    def availability(self) -> EnvironmentAvailability:
        return self._availability

    @property
    def operations(self) -> EnvironmentOperations:
        return self._operations

    async def open(self, *, execution_id: str) -> None:
        if self.target is None:
            raise provider_error(_KEY, "provider_state_invalid", EnvironmentProviderErrorCategory.INVALID)
        container_id = self.target.container_id
        with engine_errors(mutation=False):
            container = await asyncio.to_thread(self._lookup)
            if container is None:
                raise _missing()
            if container.status != "running":
                raise EnvironmentError("Docker container is stopped", code="environment_unavailable")
            self.commands = DockerCommands(self.runtime.engine, container_id, self.config)
            self.commands.execution_id = execution_id
            await self._check_initialized(self.commands)
            self.retention = LocalRetentionStore(
                root=await create_retention_root(prefix="a13n-docker-output-"),
                execution_id=execution_id,
                generation=container_id,
                max_spool_bytes=self.config.max_spool_bytes,
            )
            self.processes = DockerProcesses(self.commands, self.environment_id, self.retention)
            self._operations = EnvironmentOperations(
                files=GuestFiles(self.commands),
                shell=self.processes,
                processes=self.processes,
                outputs=self.retention,
                ports=GuestPorts(self.commands),
            )
            self._availability = EnvironmentAvailability(
                status="available", ready_families=self.descriptor.operation_families
            )

    async def check_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        with engine_errors(mutation=False):
            container = await asyncio.to_thread(self._lookup)
            if container is None:
                raise EnvironmentError("Docker container is absent", code="environment_unavailable")
            if container.status != "running":
                raise EnvironmentError("Docker container is not running", code="environment_unavailable")

    async def close(self) -> None:
        self._availability = EnvironmentAvailability(status="unavailable")
        self._operations = EnvironmentOperations()
        try:
            if self.processes is not None:
                await self.processes.close()
        finally:
            if self.commands is not None:
                self.commands.closed = True
            if self.retention is not None:
                await self.retention.close()
