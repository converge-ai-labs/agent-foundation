"""Docker lifecycle provider."""

from __future__ import annotations

import asyncio
import os
import socket

from pydantic import BaseModel, Field

from ..eip.binding import configured_descriptor
from ..management import Environment, EnvironmentProvider, HostLocalProviderConfiguration, ProviderRuntimeContext
from ..models import EnvironmentDescriptor, EnvironmentState
from .configuration import DockerProviderConfiguration, DockerProviderStateData, DockerTargetConfiguration
from .runtime import DirectoryDockerBootstrapStore, DockerProviderRuntime, DockerSDKEngine

_PROVIDER_KEY = "a13n.docker"


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
    def display_name(self) -> str:
        return "Docker"

    @property
    def key(self) -> str:
        return _PROVIDER_KEY

    @property
    def configuration_models(self) -> dict[str, type[BaseModel]]:
        return {"1": DockerProviderConfiguration}

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
