"""Direct Local configuration and provider binding."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from a13n_environment_provider import (
    DirectLocalProviderConfiguration,
    DirectLocalShellProfile,
)

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
from .files import LocalFileOperator
from .processes import LocalPortOperator, LocalProcessManager, LocalShell
from .retention import LocalRetentionStore


@dataclass(frozen=True, slots=True)
class _DirectLocalFilePolicy:
    max_value_bytes: int


@dataclass(frozen=True, slots=True)
class _DirectLocalProcessPolicy:
    allowed_executables: frozenset[Path]
    allowed_environment_keys: frozenset[str]
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


@dataclass(frozen=True, slots=True)
class _DirectLocalBindingConfiguration:
    environment_id: str
    root_path: Path
    read_only: bool
    files: _DirectLocalFilePolicy
    shell_profiles: tuple[DirectLocalShellProfile, ...]
    processes: _DirectLocalProcessPolicy
    outputs: _DirectLocalOutputPolicy
    ports: _DirectLocalPortPolicy

    @classmethod
    def from_provider(
        cls,
        configuration: DirectLocalProviderConfiguration,
    ) -> _DirectLocalBindingConfiguration:
        return cls(
            environment_id=configuration.environment_id,
            root_path=configuration.root.path,
            read_only=configuration.root.read_only,
            files=_DirectLocalFilePolicy(max_value_bytes=configuration.max_value_bytes),
            shell_profiles=configuration.shell_profiles,
            processes=_DirectLocalProcessPolicy(
                allowed_executables=configuration.allowed_executables,
                allowed_environment_keys=configuration.allowed_environment_keys,
                max_concurrent_processes=configuration.max_concurrent_processes,
                max_wall_time_seconds=configuration.max_wall_time_seconds,
                terminate_grace_seconds=configuration.terminate_grace_seconds,
            ),
            outputs=_DirectLocalOutputPolicy(
                max_buffer_bytes=configuration.max_buffer_bytes,
                max_spool_bytes=configuration.max_spool_bytes,
            ),
            ports=_DirectLocalPortPolicy(allowed_ports=configuration.allowed_ports),
        )


class _BoundDirectLocalProvider(BoundEnvironmentProvider):
    def __init__(
        self,
        *,
        environment_id: str,
        generation: str,
        descriptor: EnvironmentDescriptor,
        operations: EnvironmentProviderOperations,
    ) -> None:
        self._environment_id = environment_id
        self._generation = generation
        self._descriptor = descriptor
        self._operations = operations
        self._availability = EnvironmentAvailability(
            status="available",
            ready_families=descriptor.operation_families,
        )

    @property
    def provider_type(self) -> str:
        return "a13n.direct-local"

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

    async def ensure_ready(self, operations: frozenset[str]) -> None:
        if not operations <= self._descriptor.operation_families:
            raise EnvironmentError("Direct Local operation family is unsupported.", code="environment_unsupported")

    async def export_state(self, *, max_bytes: int) -> EnvironmentBindingState | None:
        del max_bytes
        return None

    async def restore_state(self, state: EnvironmentBindingState) -> None:
        del state
        raise EnvironmentError("Direct Local has no portable state entry.", code="environment_state_invalid")


class DirectLocalEnvironmentProviderBinding(EnvironmentProviderBinding):
    """Single-use direct process-local root and output provider."""

    def __init__(self, configuration: DirectLocalProviderConfiguration) -> None:
        self.configuration = _DirectLocalBindingConfiguration.from_provider(configuration)
        self._used = False
        self._discarded = False

    @property
    def provider_type(self) -> str:
        return "a13n.direct-local"

    @property
    def environment_id(self) -> str:
        return self.configuration.environment_id

    @asynccontextmanager
    async def bind(
        self,
        *,
        run_id: str,
        instance,
        binding_id: str,
        binding_version: int,
    ) -> AsyncGenerator[BoundEnvironmentProvider]:
        del run_id, instance
        if self._used or self._discarded:
            raise EnvironmentError("Direct Local provider binding is single-use.", code="environment_binding_reused")
        self._used = True
        configured = self.configuration.root_path
        root: Path | None = None
        retention: LocalRetentionStore | None = None
        processes: LocalProcessManager | None = None
        retention_root: Path | None = None
        try:
            root = await asyncio.to_thread(_resolve_shared_root, configured)
            generation = f"generation-{uuid4().hex[:16]}"
            files = LocalFileOperator(
                root=root,
                read_only=self.configuration.read_only,
                policy=self.configuration.files,
                binding_id=binding_id,
                binding_version=binding_version,
                generation=generation,
            )
            process_enabled = bool(
                self.configuration.processes.allowed_executables or self.configuration.shell_profiles
            )
            if process_enabled:
                retention_root = await _create_retention_root()
                retention = LocalRetentionStore(
                    root=retention_root,
                    binding_id=binding_id,
                    binding_version=binding_version,
                    generation=generation,
                    max_spool_bytes=self.configuration.outputs.max_spool_bytes,
                )
                processes = LocalProcessManager(
                    files=files,
                    retention=retention,
                    policy=self.configuration.processes,
                    output_policy=self.configuration.outputs,
                    shell_profiles=self.configuration.shell_profiles,
                    provider_type=self.provider_type,
                    environment_id=self.configuration.environment_id,
                    binding_id=binding_id,
                    binding_version=binding_version,
                    generation=generation,
                )
            shell = LocalShell(processes) if processes is not None else None
            ports = LocalPortOperator(self.configuration.ports) if self.configuration.ports.allowed_ports else None
            read_file_actions = {
                EnvironmentAction.FILE_STAT,
                EnvironmentAction.FILE_READ_TEXT,
                EnvironmentAction.FILE_READ_BYTES,
                EnvironmentAction.FILE_LIST,
                EnvironmentAction.FILE_QUERY,
                EnvironmentAction.FILE_SEARCH_TEXT,
                EnvironmentAction.FILE_COPY_SOURCE,
            }
            file_actions = {action for action in EnvironmentAction if action.value.startswith("environment.file.")}
            permissions = set(read_file_actions if self.configuration.read_only else file_actions)
            families: set[EnvironmentOperationFamily] = {"files"}
            if shell is not None:
                permissions.add(EnvironmentAction.SHELL_EXEC)
                families.add("shell")
            if processes is not None:
                permissions.update(
                    action for action in EnvironmentAction if action.value.startswith("environment.process.")
                )
                permissions.update({EnvironmentAction.OUTPUT_READ, EnvironmentAction.OUTPUT_RELEASE})
                families.update({"processes", "outputs"})
            if ports is not None:
                permissions.update({EnvironmentAction.PORT_INSPECT, EnvironmentAction.PORT_WAIT})
                families.add("ports")
            limits: dict[str, int | float] = {
                "max_value_bytes": self.configuration.files.max_value_bytes,
            }
            if processes is not None:
                limits.update(
                    {
                        "max_wall_time_seconds": self.configuration.processes.max_wall_time_seconds,
                        "max_buffer_bytes": self.configuration.outputs.max_buffer_bytes,
                        "max_spool_bytes": self.configuration.outputs.max_spool_bytes,
                    }
                )
            descriptor = EnvironmentDescriptor(
                generation=generation,
                operation_families=frozenset(families),
                permissions=EnvironmentPermissionSet(operations=frozenset(permissions)),
                limits=limits,
                mounts=(
                    EnvironmentMountDescriptor(
                        name="root",
                        path="/",
                        read_only=self.configuration.read_only,
                    ),
                ),
            )
            yield _BoundDirectLocalProvider(
                environment_id=self.configuration.environment_id,
                generation=generation,
                descriptor=descriptor,
                operations=EnvironmentProviderOperations(
                    files=files,
                    shell=shell,
                    processes=processes,
                    ports=ports,
                    outputs=retention,
                ),
            )
        finally:
            try:
                if processes is not None:
                    await processes.close()
            finally:
                try:
                    if retention is not None:
                        await retention.close()
                    elif retention_root is not None:
                        await asyncio.to_thread(shutil.rmtree, retention_root, True)
                finally:
                    root = None

    async def discard(self) -> None:
        self._discarded = True


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
        if allocation.done() and not allocation.cancelled():
            try:
                allocated = allocation.result()
            except BaseException:
                pass
        if allocated is not None:
            cleanup = asyncio.create_task(asyncio.to_thread(shutil.rmtree, allocated, True))
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
        raise cancellation


def _resolve_shared_root(path: Path) -> Path:
    try:
        root = path.resolve(strict=True)
        is_directory = root.is_dir()
    except FileNotFoundError as exc:
        raise EnvironmentError("Direct Local shared root does not exist.", code="environment_not_found") from exc
    except PermissionError as exc:
        raise EnvironmentError("Direct Local shared root is not accessible.", code="environment_denied") from exc
    except OSError as exc:
        raise EnvironmentError(
            "Direct Local could not inspect the shared root.", code="environment_provider_failure"
        ) from exc
    if not is_directory:
        raise EnvironmentError("Direct Local shared root is not a directory.", code="environment_request_invalid")
    return root
