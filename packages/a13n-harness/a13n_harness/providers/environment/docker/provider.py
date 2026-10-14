"""Docker provider implementation."""

from __future__ import annotations

import asyncio
import contextlib

from anyio import move_on_after
from pydantic import BaseModel, JsonValue

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory, provider_error
from a13n_harness.environment.models import EnvironmentDescriptor, EnvironmentState, decode_target_state
from a13n_harness.providers.environment._backend import ExecutionBackend
from a13n_harness.providers.environment._backend_factory import BackendFactory
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.docker.configuration import (
    DockerEnvironmentConfiguration,
    DockerProviderStateData,
)
from a13n_harness.providers.environment.docker.execution import DockerExecution
from a13n_harness.providers.environment.docker.runtime import DockerProviderRuntime, DockerSDKEngine
from a13n_harness.providers.environment.docker.shared import _ENGINE_TEARDOWN_SECONDS, _KEY, _fingerprint, descriptor
from a13n_harness.providers.environment.docker.shared import (
    DockerConnectionConfiguration as DockerConnectionConfiguration,
)


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
    return DockerProviderRuntime(engine, configuration.envd_http, configuration._endpoint_policy)


def _backend_identity(configuration: DockerConnectionConfiguration) -> dict[str, JsonValue]:
    # Adding optional HTTP routing must not invalidate saved native accounts.
    values = configuration.model_dump(mode="json")
    if configuration.envd_http is None:
        values.pop("envd_http", None)
    return values


def _describe(configuration: DockerEnvironmentConfiguration) -> EnvironmentDescriptor:
    if not isinstance(configuration, DockerEnvironmentConfiguration):
        raise TypeError("Docker requires DockerEnvironmentConfiguration")
    if configuration.execution == "envd_http":
        from a13n_harness.providers.environment.eip.binding import configured_descriptor

        return configured_descriptor(working_directory="/workspace")
    return descriptor("unprepared", configuration)


def _identity(*, configuration: DockerEnvironmentConfiguration, state: EnvironmentState | None) -> str:
    data = decode_target_state(_KEY, state, DockerProviderStateData, fingerprint=_fingerprint(configuration))
    if data is None:
        raise provider_error(_KEY, "provider_state_required", EnvironmentProviderErrorCategory.INVALID)
    return data.container_id


def _construct_execution(
    *,
    configuration: DockerEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: DockerProviderRuntime | None,
) -> ExecutionBackend:
    if not isinstance(configuration, DockerEnvironmentConfiguration) or runtime is None:
        raise TypeError("Docker requires typed configuration and runtime")
    if configuration.execution == "envd_http":
        from a13n_harness.providers.environment.docker.http import HttpReference, http_execution

        return http_execution(HttpReference(configuration, environment_id, state, runtime))
    return DockerExecution(configuration, environment_id, state, runtime)


_factory = BackendFactory(
    key=_KEY,
    environment_model=DockerEnvironmentConfiguration,
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
    describe_environment=_describe,
    backend_identity=_backend_identity,
    target_identity=_identity,
)
