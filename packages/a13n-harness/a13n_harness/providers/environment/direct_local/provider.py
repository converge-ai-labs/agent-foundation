from __future__ import annotations

import asyncio
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from .._local_identity import local_backing_identity
from .._local_retention import LocalRetentionStore
from ..definition import EnvironmentProviderDefinition
from ..errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderErrorContext,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
)
from ..management import Environment, EnvironmentProviderConfiguration
from ..models import (
    FILE_ACTIONS,
    EnvironmentAction,
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentMountDescriptor,
    EnvironmentOperationFamily,
    EnvironmentPermissionSet,
    EnvironmentState,
)
from ..operations import EnvironmentOperations
from .configuration import DirectLocalEnvironmentConfiguration
from .files import LocalFileOperator
from .processes import LocalPortOperator, LocalProcessManager, LocalShell

_PROVIDER_KEY = "direct_local"


@dataclass(frozen=True, slots=True)
class _DirectLocalFilePolicy:
    max_value_bytes: int


@dataclass(frozen=True, slots=True)
class _DirectLocalProcessPolicy:
    allowed_executables: frozenset[Path]
    allowed_environment_keys: frozenset[str] | None
    inherit_environment: bool
    max_concurrent_processes: int
    max_wall_time_seconds: float
    terminate_grace_seconds: float


@dataclass(frozen=True, slots=True)
class _DirectLocalOutputPolicy:
    max_buffer_bytes: int
    max_spool_bytes: int


@dataclass(frozen=True, slots=True)
class _DirectLocalPortPolicy:
    allowed_ports: frozenset[int]


def _describe(configuration: DirectLocalEnvironmentConfiguration) -> EnvironmentDescriptor:
    if not isinstance(configuration, DirectLocalEnvironmentConfiguration):
        raise TypeError("Unexpected Provider recipe")
    return _descriptor(configuration, "unprepared")


def _identity(*, configuration: DirectLocalEnvironmentConfiguration, state: EnvironmentState | None) -> str | None:
    if not isinstance(configuration, DirectLocalEnvironmentConfiguration) or state is not None:
        raise ValueError("Local Providers require a valid stateless workspace configuration")
    return str(configuration.root.path)


def _construct(
    *,
    configuration: DirectLocalEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: object | None,
    operation_id: str,
    allow_create: bool,
) -> Environment:
    """Direct Local needs no collaborator: the host filesystem is the target."""
    del runtime, operation_id, allow_create
    if state is not None:
        raise _provider_error(
            "Direct Local is stateless and does not accept Environment state.",
            code="provider_state_invalid",
            category=EnvironmentProviderErrorCategory.INVALID,
        )
    return DirectLocalEnvironment(configuration, environment_id=environment_id)


DIRECT_LOCAL = EnvironmentProviderDefinition(
    type="direct_local",
    display_name="Direct Local",
    configuration_model=EnvironmentProviderConfiguration,
    environment_model=DirectLocalEnvironmentConfiguration,
    construct=_construct,
    describe_environment=_describe,
    target_identity=_identity,
    supports_stop=True,
    supports_destroy=True,
)


class DirectLocalEnvironment(Environment):
    def __init__(self, configuration: DirectLocalEnvironmentConfiguration, *, environment_id: str) -> None:
        super().__init__(None)
        self._environment_id = environment_id
        self._configuration = configuration.model_copy(deep=True)
        self._descriptor = _descriptor(configuration, "unprepared")
        self._availability = EnvironmentAvailability(status="preparing")
        self._operations = EnvironmentOperations()
        self._files: LocalFileOperator | None = None
        self._processes: LocalProcessManager | None = None
        self._retention: LocalRetentionStore | None = None
        self._retention_root: Path | None = None

    @property
    def provider_key(self) -> str:
        return _PROVIDER_KEY

    @property
    def environment_id(self) -> str:
        return self._environment_id

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

    async def _prepare(
        self,
        *,
        mount_id: str,
    ) -> None:

        root = await asyncio.to_thread(_resolve_shared_root, self._configuration.root.path)
        policy = self._configuration.model_dump(mode="json")
        policy["root"] = {"path": str(root)}
        for field in ("allowed_executables", "allowed_environment_keys", "allowed_ports"):
            if policy[field] is not None:
                policy[field] = sorted(policy[field])
        backing_identity = await asyncio.to_thread(
            local_backing_identity, provider_key=_PROVIDER_KEY, roots=(root,), policy=policy
        )
        generation = f"generation-{uuid4().hex[:16]}"
        files = LocalFileOperator(
            root=root,
            policy=_DirectLocalFilePolicy(max_value_bytes=self._configuration.max_value_bytes),
            mount_id=mount_id,
            generation=generation,
        )
        self._files = files
        process_enabled = bool(self._configuration.allowed_executables or self._configuration.shell_profiles)
        processes: LocalProcessManager | None = None
        retention: LocalRetentionStore | None = None
        if process_enabled:
            retention_root = await _create_retention_root()
            self._retention_root = retention_root
            retention = LocalRetentionStore(
                root=retention_root,
                mount_id=mount_id,
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
                provider_type=_PROVIDER_KEY,
                environment_id=self.environment_id,
                mount_id=mount_id,
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

    def _bind_mount(self, mount_id: str) -> None:
        files = self._operations.files
        if isinstance(files, LocalFileOperator):
            files.bind_mount(mount_id)
        if self._processes is not None:
            self._processes.bind_mount(mount_id)
        if self._retention is not None:
            self._retention.bind_mount(mount_id)

    async def _ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        missing = operations - self.descriptor.operation_families
        if missing:
            raise _operation_error("Direct Local operation family is unsupported.", "environment_unsupported")

    async def _close(self) -> None:
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

    async def reconcile(self) -> Literal["running", "stopped", "absent"]:
        # These adapters own no durable daemon; their process-local resources
        # end with their owner. Workspace paths are externally retained.
        return "stopped"

    async def _stop(self) -> None:
        return None

    async def _destroy(self) -> None:
        return None


def _resolve_shared_root(path: Path) -> Path:
    try:
        root = path.resolve(strict=True)
        if not root.is_dir():
            raise _operation_error("Direct Local root is not a directory.", "environment_request_invalid")
        return root
    except FileNotFoundError as error:
        raise _operation_error("Direct Local root does not exist.", "environment_not_found") from error
    except PermissionError as error:
        raise _operation_error("Direct Local root is not accessible.", "environment_denied") from error
    except OSError as error:
        raise _operation_error("Direct Local root could not be inspected.", "environment_provider_failure") from error


async def _create_retention_root() -> Path:
    allocation = asyncio.create_task(asyncio.to_thread(tempfile.mkdtemp, prefix="a13n-output-"))
    try:
        return Path(await asyncio.shield(allocation))
    except asyncio.CancelledError as cancellation:
        allocated: str | None = None
        while not allocation.done():
            try:
                allocated = await asyncio.shield(allocation)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if allocated is not None:
            await asyncio.to_thread(shutil.rmtree, allocated, True)
        raise cancellation


def _provider_error(
    message: str,
    *,
    code: str,
    category: EnvironmentProviderErrorCategory,
) -> EnvironmentProviderError:
    return EnvironmentProviderError(
        message,
        code=code,
        category=category,
        certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
        recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
        context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
    )


def _operation_error(message: str, code: str):
    from ..models import EnvironmentError

    return EnvironmentError(message, code=code)


def _descriptor(
    configuration: DirectLocalEnvironmentConfiguration, generation: str, *, backing_identity: str | None = None
) -> EnvironmentDescriptor:
    process_enabled = bool(configuration.allowed_executables or configuration.shell_profiles)
    permissions = set(FILE_ACTIONS)
    families: set[EnvironmentOperationFamily] = {"files"}
    if process_enabled:
        permissions.add(EnvironmentAction.SHELL_EXEC)
        families.add("shell")
        permissions.update(
            action
            for action in EnvironmentAction
            if action.value.startswith("environment.process.") and action != EnvironmentAction.PROCESS_LIST
        )
        permissions.update({EnvironmentAction.OUTPUT_READ, EnvironmentAction.OUTPUT_RELEASE})
        families.update({"processes", "outputs"})
    if configuration.allowed_ports:
        permissions.update({EnvironmentAction.PORT_INSPECT, EnvironmentAction.PORT_WAIT})
        families.add("ports")
    limits: dict[str, int | float] = {"max_value_bytes": configuration.max_value_bytes}
    if process_enabled:
        limits.update(
            max_wall_time_seconds=configuration.max_wall_time_seconds,
            max_buffer_bytes=configuration.max_buffer_bytes,
            max_spool_bytes=configuration.max_spool_bytes,
        )
    return EnvironmentDescriptor(
        generation=generation,
        backing_identity=backing_identity,
        operation_families=frozenset(families),
        permissions=EnvironmentPermissionSet(operations=frozenset(permissions)),
        limits=limits,
        mounts=(EnvironmentMountDescriptor(name="root", path="/"),),
    )
