from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .._local_identity import local_backing_identity
from ..attachments import DeviceEIPSessionSource
from ..eip import EIPEnvironmentSession, open_eip_environment
from ..eip.binding import configured_descriptor
from ..errors import EnvironmentProviderError
from ..errors import EnvironmentProviderErrorCategory as Category
from ..management import Environment, EnvironmentProvider, HostLocalProviderConfiguration, ProviderRuntimeContext
from ..models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentOperationFamily,
    EnvironmentState,
)
from ..operations import EnvironmentOperations
from ..remote_envd.environment import provider_error
from .configuration import LocalEnvdProviderConfiguration
from .runtime import LocalEnvdProviderRuntime, TemporaryLocalEnvdRuntimeAllocator, resolve_a13n_envd_executable

_PROVIDER_KEY = "a13n.local-envd"


class LocalEnvdEnvironmentProvider(EnvironmentProvider):
    """Inert Provider; the Host runtime owns the shared native Device."""

    provider_configuration_model = HostLocalProviderConfiguration

    @property
    def display_name(self) -> str:
        return "Local Envd"

    @property
    def key(self) -> str:
        return _PROVIDER_KEY

    @property
    def configuration_models(self) -> dict[str, type[BaseModel]]:
        return {"1": LocalEnvdProviderConfiguration}

    async def create_runtime(
        self, *, configuration: BaseModel, credential: BaseModel | None, context: ProviderRuntimeContext
    ) -> LocalEnvdProviderRuntime:
        executable = await asyncio.to_thread(resolve_a13n_envd_executable)
        return LocalEnvdProviderRuntime(
            executable=executable, allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator()
        )

    def describe_configuration(self, configuration: BaseModel) -> EnvironmentDescriptor:
        if not isinstance(configuration, LocalEnvdProviderConfiguration):
            raise TypeError("Local Envd requires LocalEnvdProviderConfiguration")
        return configured_descriptor()

    def target_identity(self, *, configuration: BaseModel, state: EnvironmentState | None) -> str | None:
        self.describe_configuration(configuration)
        if state is not None:
            raise provider_error(self.key, "provider_state_invalid", Category.INVALID)
        # The Host runtime chooses the Device; cwd is not target identity.
        return None

    def create_environment(
        self,
        *,
        configuration: BaseModel,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: object | None = None,
    ) -> Environment:
        if not isinstance(configuration, LocalEnvdProviderConfiguration):
            raise TypeError("Local Envd requires LocalEnvdProviderConfiguration")
        if state is not None:
            raise provider_error(self.key, "provider_state_invalid", Category.INVALID)
        if not isinstance(runtime, LocalEnvdProviderRuntime):
            raise TypeError("Local Envd requires LocalEnvdProviderRuntime")
        return LocalEnvdEnvironment(configuration, runtime, environment_id=environment_id)


class LocalEnvdEnvironment(Environment):
    def __init__(
        self,
        configuration: LocalEnvdProviderConfiguration,
        runtime: LocalEnvdProviderRuntime,
        *,
        environment_id: str,
    ) -> None:
        super().__init__(None)
        self._environment_id = environment_id
        self._configuration = configuration.model_copy(deep=True)
        self._runtime = runtime
        self._descriptor = configured_descriptor()
        self._availability = EnvironmentAvailability(status="preparing")
        self._operations = EnvironmentOperations()
        self._eip_scope: AbstractAsyncContextManager[EIPEnvironmentSession] | None = None
        self._bound: EIPEnvironmentSession | None = None
        self._attempted = False

    @property
    def provider_key(self) -> str:
        return _PROVIDER_KEY

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
    def operations(self) -> EnvironmentOperations:
        return self._operations

    async def _prepare(
        self,
        *,
        thread_id: str,
        run_id: str,
        agent_instance_id: str,
        mount_id: str,
        host_refs: Mapping[str, str],
    ) -> None:
        if self._attempted:
            raise provider_error(self.provider_key, "provider_runtime_consumed", Category.UNAVAILABLE)
        self._attempted = True
        try:
            device = await self._runtime.acquire_device()
            scope = open_eip_environment(
                provider_key=self.provider_key,
                environment_id=self.environment_id,
                session_source=DeviceEIPSessionSource(device),
                mount_id=mount_id,
                device_id=device.descriptor.device_id,
                working_directory=self._configuration.working_directory,
                required_methods=frozenset(self._configuration.required_methods),
            )
            self._bound = await scope.__aenter__()
            self._eip_scope = scope
            launch = self._runtime.effective_configuration
            directory = self._bound.descriptor.working_directory
            assert directory is not None
            # Device drive paths have a leading slash; UNC paths retain theirs.
            native_directory = directory
            if os.name == "nt" and len(directory) >= 4 and directory[2:4] == ":/":
                native_directory = directory[1:]
            elif os.name == "nt" and directory.startswith("/UNC/"):
                native_directory = "//" + directory[5:]
            identity = await asyncio.to_thread(
                local_backing_identity,
                provider_key=self.provider_key,
                roots=(Path(native_directory), *launch.trusted_executable_roots),
                policy={"executable": str(self._runtime.executable), "launch": launch.model_dump(mode="json")},
            )
            self._descriptor = self._bound.descriptor.model_copy(update={"backing_identity": identity})
            self._operations = self._bound.operations
            self._availability = self._bound.availability
        except BaseException as error:
            try:
                await self._close()
            except Exception as cleanup_error:
                # Keep the preparation failure primary and retain cleanup evidence.
                error.__cause__ = cleanup_error
            if not isinstance(error, Exception) or isinstance(error, (EnvironmentError, EnvironmentProviderError)):
                raise
            raise provider_error(self.provider_key, "provider_session_failed", Category.UNAVAILABLE) from error

    def _bind_mount(self, mount_id: str) -> None:
        if self._bound is not None:
            self._bound.bind_mount(mount_id)
            self._operations = self._bound.operations

    async def _ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        if self._bound is None:
            raise EnvironmentError("Local Envd Session is unavailable", code="environment_unavailable")
        try:
            await self._bound.ensure_ready(operations)
        finally:
            self._availability = self._bound.availability

    async def _close(self) -> None:
        self._availability = EnvironmentAvailability(status="unavailable")
        self._operations = EnvironmentOperations()
        scope, self._eip_scope = self._eip_scope, None
        self._bound = None
        if scope is not None:
            try:
                await scope.__aexit__(None, None, None)
            except (EnvironmentError, EnvironmentProviderError):
                raise
            except Exception as error:
                raise provider_error(
                    self.provider_key, "provider_session_close_failed", Category.UNAVAILABLE
                ) from error

    async def reconcile(self) -> Literal["running", "stopped", "absent"]:
        return "stopped"

    async def _stop(self) -> None:
        raise provider_error(self.provider_key, "provider_operation_unsupported", Category.UNSUPPORTED)

    async def _destroy(self) -> None:
        raise provider_error(self.provider_key, "provider_operation_unsupported", Category.UNSUPPORTED)
