from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, JsonValue, ValidationError

from ..errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderErrorContext,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
)
from ..management import Environment, EnvironmentProvider
from ..models import (
    EnvironmentAction,
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentMountDescriptor,
    EnvironmentOperationFamily,
    EnvironmentPermissionSet,
    EnvironmentState,
)
from ..operations import EnvironmentOperations
from .configuration import DirectLocalProviderConfiguration
from .files import LocalFileOperator
from .processes import LocalPortOperator, LocalProcessManager, LocalShell
from .retention import LocalRetentionStore

_PROVIDER_KEY = "a13n.direct-local"
_CONFIGURATION_VERSION = "1"


@dataclass(frozen=True, slots=True)
class DirectLocalProviderRuntime:
    """Explicit empty runtime collaborator for Direct Local."""


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


class DirectLocalEnvironmentProvider(EnvironmentProvider):
    @property
    def key(self) -> str:
        return _PROVIDER_KEY

    @property
    def configuration_versions(self) -> frozenset[str]:
        return frozenset({_CONFIGURATION_VERSION})

    @property
    def provider_key(self) -> str:
        return self.key

    @property
    def connection_versions(self) -> frozenset[str]:
        return self.configuration_versions

    def validate_configuration(self, *, schema_version: str, value: JsonValue) -> BaseModel:
        if schema_version != _CONFIGURATION_VERSION:
            raise _provider_error(
                "Direct Local configuration version is unsupported.",
                code="provider_schema_unsupported",
                category=EnvironmentProviderErrorCategory.UNSUPPORTED,
            )
        try:
            return DirectLocalProviderConfiguration.model_validate(value)
        except ValidationError as error:
            raise _provider_error(
                "Direct Local configuration is invalid.",
                code="provider_spec_invalid",
                category=EnvironmentProviderErrorCategory.INVALID,
            ) from error

    def create_environment(
        self,
        *,
        configuration: BaseModel,
        state: EnvironmentState | None,
        runtime: object | None = None,
    ) -> Environment:
        if not isinstance(configuration, DirectLocalProviderConfiguration):
            raise TypeError("Direct Local requires DirectLocalProviderConfiguration")
        if state is not None:
            raise _provider_error(
                "Direct Local is stateless and does not accept Environment state.",
                code="provider_state_invalid",
                category=EnvironmentProviderErrorCategory.INVALID,
            )
        if runtime is not None and not isinstance(runtime, DirectLocalProviderRuntime):
            raise TypeError("Direct Local runtime must be DirectLocalProviderRuntime or None")
        return DirectLocalEnvironment(configuration)

    def validate_connection(self, *, schema_version: str, parameters: JsonValue) -> BaseModel:
        if schema_version != _CONFIGURATION_VERSION:
            raise _provider_error(
                "Direct Local connection version is unsupported.",
                code="provider_schema_unsupported",
                category=EnvironmentProviderErrorCategory.UNSUPPORTED,
            )
        try:
            connection = DirectLocalProviderConfiguration.model_validate(parameters)
        except ValidationError as error:
            raise _provider_error(
                "Direct Local connection is invalid.",
                code="provider_connection_invalid",
                category=EnvironmentProviderErrorCategory.INVALID,
            ) from error
        root = connection.root.model_copy(
            update={"path": Path(os.path.normpath(str(connection.root.path)))},
        )
        return connection.model_copy(update={"root": root})

    def target_key(self, *, connection: BaseModel) -> str:
        if not isinstance(connection, DirectLocalProviderConfiguration):
            raise TypeError("Direct Local requires DirectLocalProviderConfiguration")
        return str(connection.root.path)

    def create_attachment_environment(
        self,
        *,
        connection: BaseModel,
        runtime: object,
    ) -> Environment:
        if not isinstance(connection, DirectLocalProviderConfiguration):
            raise TypeError("Direct Local attachment requires DirectLocalProviderConfiguration")
        if not isinstance(runtime, DirectLocalProviderRuntime):
            raise TypeError("Direct Local attachment requires DirectLocalProviderRuntime")
        return DirectLocalEnvironment(connection, exact_target=True)


class DirectLocalEnvironment(Environment):
    def __init__(self, configuration: DirectLocalProviderConfiguration, *, exact_target: bool = False) -> None:
        super().__init__(None)
        self._configuration = configuration.model_copy(deep=True)
        self._exact_target = exact_target
        self._descriptor: EnvironmentDescriptor | None = None
        self._availability = EnvironmentAvailability(status="preparing")
        self._operations = EnvironmentOperations()
        self._processes: LocalProcessManager | None = None
        self._retention: LocalRetentionStore | None = None
        self._retention_root: Path | None = None

    @property
    def provider_key(self) -> str:
        return _PROVIDER_KEY

    @property
    def environment_id(self) -> str:
        return self._configuration.environment_id

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

    async def _enter(
        self,
        *,
        thread_id: str,
        run_id: str,
        agent_instance_id: str,
        mount_id: str,
        host_refs: Mapping[str, str],
    ) -> None:
        del thread_id, run_id, agent_instance_id, host_refs
        root = await asyncio.to_thread(_resolve_shared_root, self._configuration.root.path)
        if self._exact_target and root != self._configuration.root.path:
            raise _provider_error(
                "Direct Local attachment root resolves to a different target.",
                code="provider_target_conflict",
                category=EnvironmentProviderErrorCategory.CONFLICT,
            )
        generation = f"generation-{uuid4().hex[:16]}"
        files = LocalFileOperator(
            root=root,
            read_only=self._configuration.root.read_only,
            policy=_DirectLocalFilePolicy(max_value_bytes=self._configuration.max_value_bytes),
            mount_id=mount_id,
            generation=generation,
        )
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
        read_actions = {
            EnvironmentAction.FILE_STAT,
            EnvironmentAction.FILE_READ_TEXT,
            EnvironmentAction.FILE_READ_BYTES,
            EnvironmentAction.FILE_LIST,
            EnvironmentAction.FILE_QUERY,
            EnvironmentAction.FILE_SEARCH_TEXT,
            EnvironmentAction.FILE_COPY_SOURCE,
        }
        file_actions = {action for action in EnvironmentAction if action.value.startswith("environment.file.")}
        permissions = set(read_actions if self._configuration.root.read_only else file_actions)
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
        limits: dict[str, int | float] = {"max_value_bytes": self._configuration.max_value_bytes}
        if processes is not None:
            limits.update(
                max_wall_time_seconds=self._configuration.max_wall_time_seconds,
                max_buffer_bytes=self._configuration.max_buffer_bytes,
                max_spool_bytes=self._configuration.max_spool_bytes,
            )
        self._descriptor = EnvironmentDescriptor(
            generation=generation,
            operation_families=frozenset(families),
            permissions=EnvironmentPermissionSet(operations=frozenset(permissions)),
            limits=limits,
            mounts=(EnvironmentMountDescriptor(name="root", path="/", read_only=self._configuration.root.read_only),),
        )
        self._processes = processes
        self._retention = retention
        self._operations = EnvironmentOperations(
            files=files,
            shell=shell,
            processes=processes,
            ports=ports,
            outputs=retention,
        )
        self._availability = EnvironmentAvailability(status="available", ready_families=frozenset(families))

    async def _ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        missing = operations - self.descriptor.operation_families
        if missing:
            raise _operation_error("Direct Local operation family is unsupported.", "environment_unsupported")

    async def _close(self) -> None:
        self._availability = EnvironmentAvailability(status="unavailable")
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
