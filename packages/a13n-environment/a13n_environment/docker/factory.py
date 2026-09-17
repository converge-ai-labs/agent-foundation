"""Native Docker Provider; configuration never depends on a Worker hostname."""

from __future__ import annotations

import asyncio
import os

from pydantic import BaseModel, Field

from ..management import EmptyProviderConfiguration, Environment, EnvironmentProvider, ProviderRuntimeContext
from ..models import EnvironmentDescriptor, EnvironmentState
from .configuration import DockerProviderConfiguration, DockerProviderStateData
from .runtime import DockerProviderRuntime, DockerSDKEngine


class DockerBackendConfiguration(EmptyProviderConfiguration):
    docker_host: str = Field(
        default_factory=lambda: os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock"), min_length=1
    )


class DockerEnvironmentProvider(EnvironmentProvider):
    provider_configuration_model = DockerBackendConfiguration
    supports_stop = True
    supports_destroy = True

    @property
    def display_name(self) -> str:
        return "Docker"

    @property
    def key(self) -> str:
        return "a13n.docker"

    @property
    def configuration_models(self) -> dict[str, type[BaseModel]]:
        return {"1": DockerProviderConfiguration}

    async def create_runtime(
        self, *, configuration: BaseModel, credential: BaseModel | None, context: ProviderRuntimeContext
    ) -> DockerProviderRuntime:
        if not isinstance(configuration, DockerBackendConfiguration):
            raise TypeError("Docker requires DockerBackendConfiguration")
        engine = await asyncio.to_thread(DockerSDKEngine.connect, configuration.docker_host)
        return DockerProviderRuntime(engine, managed=context.managed, owns_engine=True)

    def describe_configuration(self, configuration: BaseModel) -> EnvironmentDescriptor:
        from .provider import descriptor

        if not isinstance(configuration, DockerProviderConfiguration):
            raise TypeError("Docker requires DockerProviderConfiguration")
        return descriptor("unprepared", configuration)

    def target_identity(self, *, configuration: BaseModel, state: EnvironmentState | None) -> str | None:
        return None if state is None else DockerProviderStateData.model_validate(state.state).container_id

    def create_environment(
        self,
        *,
        configuration: BaseModel,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: object | None = None,
    ) -> Environment:
        from .provider import DockerEnvironment

        if not isinstance(configuration, DockerProviderConfiguration) or not isinstance(runtime, DockerProviderRuntime):
            raise TypeError("Docker requires typed configuration and runtime")
        return DockerEnvironment(configuration, environment_id, state, runtime)
