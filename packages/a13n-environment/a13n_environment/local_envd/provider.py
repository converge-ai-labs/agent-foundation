"""Inert Local Envd Provider; the Host runtime owns the shared native Device."""

from __future__ import annotations

import asyncio
import os
from contextlib import AbstractAsyncContextManager
from pathlib import Path

from .._backend import BackendTarget
from .._backend_factory import BackendFactory
from .._local_identity import local_backing_identity
from ..attachments import DeviceEIPSessionSource
from ..definition import EnvironmentProviderDefinition
from ..eip import EIPEnvironmentSession, open_eip_environment
from ..eip.binding import configured_descriptor
from ..envd_policy import resolve_egress
from ..errors import EnvironmentProviderError, provider_error
from ..errors import EnvironmentProviderErrorCategory as Category
from ..management import HostLocalProviderConfiguration
from ..models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentOperationFamily,
    EnvironmentState,
)
from ..operations import EnvironmentOperations
from .configuration import LocalEnvdEnvironmentConfiguration
from .runtime import LocalEnvdProviderRuntime

_PROVIDER_KEY = "local_envd"


def _describe(configuration: LocalEnvdEnvironmentConfiguration) -> EnvironmentDescriptor:
    if not isinstance(configuration, LocalEnvdEnvironmentConfiguration):
        raise TypeError("Local Envd requires LocalEnvdEnvironmentConfiguration")
    return configured_descriptor(working_directory=configuration.working_directory)


def _identity(*, configuration: LocalEnvdEnvironmentConfiguration, state: EnvironmentState | None) -> str | None:
    _describe(configuration)
    if state is not None:
        raise provider_error(_PROVIDER_KEY, "provider_state_invalid", Category.INVALID)
    # The Host runtime chooses the Device; a Session working directory is not target identity.
    return None


def _construct(
    *,
    configuration: LocalEnvdEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: LocalEnvdProviderRuntime | None,
    operation_id: str,
) -> BackendTarget:
    del operation_id
    if not isinstance(configuration, LocalEnvdEnvironmentConfiguration):
        raise TypeError("Local Envd requires LocalEnvdEnvironmentConfiguration")
    if state is not None:
        raise provider_error(_PROVIDER_KEY, "provider_state_invalid", Category.INVALID)
    if runtime is None:
        raise provider_error(_PROVIDER_KEY, "provider_runtime_required", Category.INVALID)
    return LocalEnvdTarget(configuration, runtime, environment_id=environment_id)


_factory = BackendFactory(
    key=_PROVIDER_KEY, environment_model=LocalEnvdEnvironmentConfiguration, target=_construct, describe=_describe
)


LOCAL_ENVD = EnvironmentProviderDefinition(
    type=_PROVIDER_KEY,
    display_name="Local Envd",
    configuration_model=HostLocalProviderConfiguration,
    environment_model=LocalEnvdEnvironmentConfiguration,
    connector_factory=_factory.connector,
    describe_environment=_describe,
    target_identity=_identity,
    supports_managed=False,
    supports_stop=False,
    supports_destroy=False,
)


class LocalEnvdTarget(BackendTarget):
    """One fixed-cwd Session on a shared Device; a borrowed daemon runtime outlives it."""

    def __init__(
        self,
        configuration: LocalEnvdEnvironmentConfiguration,
        runtime: LocalEnvdProviderRuntime,
        *,
        environment_id: str,
    ) -> None:
        super().__init__(None)
        self._environment_id = environment_id
        self._configuration = configuration.model_copy(deep=True)
        self._runtime = runtime
        self._descriptor = configured_descriptor(working_directory=configuration.working_directory)
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

    async def open(self, *, execution_id: str) -> None:
        if self._attempted:
            raise provider_error(self.provider_key, "provider_runtime_consumed", Category.UNAVAILABLE)
        self._attempted = True
        try:
            device = await self._runtime.acquire_device()
            scope = open_eip_environment(
                provider_key=self.provider_key,
                environment_id=self.environment_id,
                session_source=DeviceEIPSessionSource(device),
                execution_id=execution_id,
                device_id=device.descriptor.device_id,
                working_directory=self._configuration.working_directory,
                required_methods=frozenset(self._configuration.required_methods),
                egress=await resolve_egress(self._configuration.egress, self._runtime.credential_resolver),
                expected_boundary=self._configuration.expected_boundary,
            )
            self._bound = await scope.__aenter__()
            self._eip_scope = scope
            launch = self._runtime.effective_configuration
            identity = await asyncio.to_thread(
                local_backing_identity,
                provider_key=self.provider_key,
                roots=(_native_directory(self._bound.descriptor.working_directory), *launch.trusted_executable_roots),
                policy={
                    "executable": str(self._runtime.executable),
                    "launch": launch.model_dump(mode="json"),
                    "boundary": device.descriptor.boundary.policy_digest,
                },
            )
            self._descriptor = self._bound.descriptor.model_copy(update={"backing_identity": identity})
            self._operations = self._bound.operations
            self._availability = self._bound.availability
        except BaseException as error:
            try:
                await self.close()
            except Exception as cleanup_error:
                # Keep the preparation failure primary and retain cleanup evidence.
                error.__cause__ = cleanup_error
            if not isinstance(error, Exception) or isinstance(error, (EnvironmentError, EnvironmentProviderError)):
                raise
            raise provider_error(self.provider_key, "provider_session_failed", Category.UNAVAILABLE) from error

    async def check_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        if self._bound is None:
            raise EnvironmentError("Local Envd Session is unavailable", code="environment_unavailable")
        try:
            await self._bound.check_ready(operations)
        finally:
            self._availability = self._bound.availability

    async def close(self) -> None:
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


def _native_directory(directory: str) -> Path:
    """Device paths are slash-rooted; Windows drive and UNC paths regain native form."""
    if os.name == "nt":  # pragma: no cover - exercised on Windows
        if len(directory) >= 4 and directory[2:4] == ":/":
            return Path(directory[1:])
        if directory.startswith("/UNC/"):
            return Path("//" + directory[5:])
    return Path(directory)
