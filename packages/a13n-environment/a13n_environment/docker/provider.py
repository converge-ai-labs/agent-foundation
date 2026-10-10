"""Docker provider implementation."""

from __future__ import annotations

import asyncio
import contextlib

from anyio import move_on_after
from pydantic import BaseModel

from .._backend import ExecutionBackend, ManagementBackend
from .._backend_factory import BackendFactory
from ..definition import EnvironmentProviderDefinition
from ..errors import (
    EnvironmentProviderErrorCategory,
    provider_error,
)
from ..models import (
    EnvironmentDescriptor,
    EnvironmentState,
    decode_target_state,
)
from .configuration import DockerEnvironmentConfiguration, DockerProviderStateData
from .execution import DockerExecution
from .management import DockerManagement
from .runtime import DockerProviderRuntime, DockerSDKEngine
from .shared import _ENGINE_TEARDOWN_SECONDS, _KEY, _fingerprint, descriptor
from .shared import DockerConnectionConfiguration as DockerConnectionConfiguration


async def _release_engine(acquisition: asyncio.Task[DockerSDKEngine]) -> None:
    """Close an engine that finishes acquiring after its caller was already cancelled."""
    with move_on_after(_ENGINE_TEARDOWN_SECONDS, shield=True), contextlib.suppress(Exception):
        engine = await acquisition
        await engine.close()


async def _runtime(*, configuration: BaseModel, credential: BaseModel | None) -> DockerProviderRuntime:
    """Acquire the engine; a cancelled caller never leaks a live Docker client."""
    del credential
    if not isinstance(configuration, DockerConnectionConfiguration):
        raise TypeError("Docker requires DockerConnectionConfiguration")
    acquisition = asyncio.create_task(asyncio.to_thread(DockerSDKEngine.connect, configuration.docker_host))
    try:
        engine = await asyncio.shield(acquisition)
    except asyncio.CancelledError:
        await _release_engine(acquisition)
        raise
    return DockerProviderRuntime(engine)


def _describe(configuration: DockerEnvironmentConfiguration) -> EnvironmentDescriptor:
    if not isinstance(configuration, DockerEnvironmentConfiguration):
        raise TypeError("Docker requires DockerEnvironmentConfiguration")
    return descriptor("unprepared", configuration)


def _identity(*, configuration: DockerEnvironmentConfiguration, state: EnvironmentState | None) -> str:
    data = decode_target_state(_KEY, state, DockerProviderStateData, fingerprint=_fingerprint(configuration))
    if data is None:
        raise provider_error(_KEY, "provider_state_required", EnvironmentProviderErrorCategory.INVALID)
    return data.container_id


def _construct_management(
    *,
    configuration: DockerEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: DockerProviderRuntime | None,
    operation_id: str,
) -> ManagementBackend:
    del operation_id
    if not isinstance(configuration, DockerEnvironmentConfiguration) or runtime is None:
        raise TypeError("Docker requires typed configuration and runtime")
    return DockerManagement(configuration, environment_id, state, runtime)


def _construct_execution(
    *,
    configuration: DockerEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: DockerProviderRuntime | None,
) -> ExecutionBackend:
    if not isinstance(configuration, DockerEnvironmentConfiguration) or runtime is None:
        raise TypeError("Docker requires typed configuration and runtime")
    return DockerExecution(configuration, environment_id, state, runtime)


_factory = BackendFactory(
    key=_KEY,
    environment_model=DockerEnvironmentConfiguration,
    management=_construct_management,
    execution=_construct_execution,
    describe=_describe,
    runtime_factory=_runtime,
    target_identity=_identity,
)

DOCKER = EnvironmentProviderDefinition(
    type="docker",
    display_name="Docker",
    configuration_model=DockerConnectionConfiguration,
    environment_model=DockerEnvironmentConfiguration,
    connector_factory=_factory.connector,
    provider_factory=_factory.provider,
    describe_environment=_describe,
    target_identity=_identity,
    supports_stop=True,
    supports_destroy=True,
)
