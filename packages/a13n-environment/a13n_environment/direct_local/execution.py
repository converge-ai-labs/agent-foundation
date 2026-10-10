"""DirectLocal execution implementation."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from uuid import uuid4

from .._backend import ExecutionBackend
from .._local_identity import local_backing_identity
from .._local_retention import LocalRetentionStore, create_retention_root
from ..models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentOperationFamily,
)
from ..operations import EnvironmentOperations
from .configuration import DirectLocalEnvironmentConfiguration
from .files import LocalFileOperator
from .processes import LocalPortOperator, LocalProcessManager, LocalShell
from .shared import (
    DirectLocalReference,
    _descriptor,
    _DirectLocalFilePolicy,
    _DirectLocalOutputPolicy,
    _DirectLocalPortPolicy,
    _DirectLocalProcessPolicy,
    _operation_error,
    _resolve_shared_root,
)


class DirectLocalExecution(DirectLocalReference, ExecutionBackend):
    def __init__(self, configuration: DirectLocalEnvironmentConfiguration, *, environment_id: str) -> None:
        super().__init__(configuration, environment_id=environment_id)
        self._descriptor = _descriptor(configuration, "unprepared")
        self._availability = EnvironmentAvailability(status="preparing")
        self._operations = EnvironmentOperations()
        self._files: LocalFileOperator | None = None
        self._processes: LocalProcessManager | None = None
        self._retention: LocalRetentionStore | None = None
        self._retention_root: Path | None = None

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        if self._descriptor is None:
            raise RuntimeError("Environment descriptor is unavailable before entry")
        return self._descriptor

    @property
    def availability(self) -> EnvironmentAvailability:
        return self._availability

    @property
    def operations(self) -> EnvironmentOperations:
        return self._operations

    async def open(
        self,
        *,
        execution_id: str,
    ) -> None:

        root = await asyncio.to_thread(_resolve_shared_root, self._configuration.root.path)
        policy = self._configuration.model_dump(mode="json")
        policy["root"] = {"path": str(root)}
        for field in ("allowed_executables", "allowed_environment_keys", "allowed_ports"):
            if policy[field] is not None:
                policy[field] = sorted(policy[field])
        backing_identity = await asyncio.to_thread(
            local_backing_identity, provider_key=self.provider_key, roots=(root,), policy=policy
        )
        generation = f"generation-{uuid4().hex[:16]}"
        files = LocalFileOperator(
            root=root,
            policy=_DirectLocalFilePolicy(max_value_bytes=self._configuration.max_value_bytes),
            execution_id=execution_id,
            generation=generation,
        )
        self._files = files
        process_enabled = bool(self._configuration.allowed_executables or self._configuration.shell_profiles)
        processes: LocalProcessManager | None = None
        retention: LocalRetentionStore | None = None
        if process_enabled:
            retention_root = await create_retention_root(prefix="a13n-output-")
            self._retention_root = retention_root
            retention = LocalRetentionStore(
                root=retention_root,
                execution_id=execution_id,
                generation=generation,
                max_spool_bytes=self._configuration.max_spool_bytes,
            )
            processes = LocalProcessManager(
                files=files,
                retention=retention,
                policy=_DirectLocalProcessPolicy(
                    allowed_executables=self._configuration.allowed_executables,
                    allowed_environment_keys=self._configuration.allowed_environment_keys,
                    inherit_environment=self._configuration.inherit_environment,
                    max_concurrent_processes=self._configuration.max_concurrent_processes,
                    max_wall_time_seconds=self._configuration.max_wall_time_seconds,
                    terminate_grace_seconds=self._configuration.terminate_grace_seconds,
                ),
                output_policy=_DirectLocalOutputPolicy(
                    max_buffer_bytes=self._configuration.max_buffer_bytes,
                    max_spool_bytes=self._configuration.max_spool_bytes,
                ),
                shell_profiles=self._configuration.shell_profiles,
                provider_type=self.provider_key,
                environment_id=self.environment_id,
                execution_id=execution_id,
                generation=generation,
            )
        ports = (
            LocalPortOperator(_DirectLocalPortPolicy(allowed_ports=self._configuration.allowed_ports))
            if self._configuration.allowed_ports
            else None
        )
        shell = LocalShell(processes) if processes is not None else None
        self._descriptor = _descriptor(self._configuration, generation, backing_identity=backing_identity)
        self._processes = processes
        self._retention = retention
        self._operations = EnvironmentOperations(
            files=files,
            shell=shell,
            processes=processes,
            ports=ports,
            outputs=retention,
        )
        self._availability = EnvironmentAvailability(
            status="available", ready_families=self.descriptor.operation_families
        )

    async def check_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        missing = operations - self.descriptor.operation_families
        if missing:
            raise _operation_error("Direct Local operation family is unsupported.", "environment_unsupported")

    async def close(self) -> None:
        self._availability = EnvironmentAvailability(status="unavailable")
        if self._files is not None:
            self._files.close()
        try:
            if self._processes is not None:
                await self._processes.close()
        finally:
            try:
                if self._retention is not None:
                    await self._retention.close()
                elif self._retention_root is not None:
                    await asyncio.to_thread(shutil.rmtree, self._retention_root, True)
            finally:
                self._operations = EnvironmentOperations()
