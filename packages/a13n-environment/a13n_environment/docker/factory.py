"""Docker lifecycle provider."""

from __future__ import annotations

import asyncio
import os
import socket

from pydantic import BaseModel, Field, JsonValue, ValidationError

from ..eip.binding import configured_descriptor
from ..errors import EnvironmentProviderErrorCategory
from ..management import Environment, EnvironmentProvider, HostLocalProviderConfiguration, ProviderRuntimeContext
from ..models import EnvironmentDescriptor, EnvironmentState
from ._errors import provider_error
from .configuration import DockerProviderConfiguration, DockerProviderStateData, DockerTargetConfiguration
from .runtime import DirectoryDockerBootstrapStore, DockerProviderRuntime, DockerSDKEngine

_PROVIDER_KEY = "a13n.docker"
_CONFIGURATION_VERSION = "1"


class DockerBackendConfiguration(HostLocalProviderConfiguration):
    docker_host: str = Field(
        default_factory=lambda: os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock"), min_length=1
    )


class DockerEnvironmentProvider(EnvironmentProvider):
    """Reusable inert Docker provider."""

    provider_configuration_model = DockerBackendConfiguration
    supports_stop = True
    supports_destroy = True

    @property
    def key(self) -> str:
        return _PROVIDER_KEY

    @property
    def configuration_versions(self) -> frozenset[str]:
        return frozenset({_CONFIGURATION_VERSION})

    def validate_configuration(self, *, schema_version: str, value: JsonValue) -> BaseModel:
        if schema_version != _CONFIGURATION_VERSION:
            raise provider_error(
                "Docker configuration version is unsupported.",
                code="provider_schema_unsupported",
                category=EnvironmentProviderErrorCategory.UNSUPPORTED,
                schema_version=schema_version,
            )
        try:
            return DockerProviderConfiguration.model_validate(value)
        except ValidationError as error:
            raise provider_error(
                "Docker configuration is invalid.",
                code="provider_spec_invalid",
                category=EnvironmentProviderErrorCategory.INVALID,
                schema_version=schema_version,
            ) from error

    async def create_runtime(
        self, *, configuration: BaseModel, credential: BaseModel | None, context: ProviderRuntimeContext
    ) -> DockerProviderRuntime:
        if not isinstance(configuration, DockerBackendConfiguration):
            raise TypeError("Docker requires DockerBackendConfiguration")
        if configuration.host_id != socket.gethostname():
            raise ValueError("Docker backend belongs to another host")
        engine = await asyncio.to_thread(DockerSDKEngine.connect, configuration.docker_host)
        return DockerProviderRuntime(
            engine=engine,
            bootstrap_store=DirectoryDockerBootstrapStore(context.storage_root / "environment-bootstrap"),
            managed=context.managed,
            owns_engine=True,
        )

    def describe_configuration(self, configuration: BaseModel) -> EnvironmentDescriptor:
        if not isinstance(configuration, DockerProviderConfiguration):
            raise TypeError("Unexpected Provider recipe")
        return configured_descriptor()

    def target_identity(self, *, configuration: BaseModel, state: EnvironmentState | None) -> str | None:
        from .provider import decode_state

        if state is None:
            return None
        data = DockerProviderStateData.model_validate(state.state)
        target = DockerTargetConfiguration(**configuration.model_dump(), environment_id=data.environment_id)
        validated = decode_state(state, target)
        assert validated is not None
        return validated.container_id

    def create_environment(
        self,
        *,
        configuration: BaseModel,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: object | None = None,
    ) -> Environment:
        from .provider import DockerEnvironment, decode_state

        if not isinstance(configuration, DockerProviderConfiguration):
            raise TypeError("Docker requires DockerProviderConfiguration")
        if not isinstance(runtime, DockerProviderRuntime):
            raise TypeError("Docker requires DockerProviderRuntime")
        native_id = environment_id
        if not runtime.managed and state is not None:
            native_id = DockerProviderStateData.model_validate(state.state).environment_id
        target = DockerTargetConfiguration(**configuration.model_dump(), environment_id=native_id)
        state_data = decode_state(state, target)
        return DockerEnvironment(target, state, state_data=state_data, runtime=runtime, environment_id=environment_id)


__all__ = ["DockerEnvironmentProvider"]
