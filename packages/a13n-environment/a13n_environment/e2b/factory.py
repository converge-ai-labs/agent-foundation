"""Inert E2B Provider registration and Host runtime construction."""

from pydantic import BaseModel

from ..management import Environment, EnvironmentProvider, ProviderRuntimeContext
from ..models import EnvironmentDescriptor, EnvironmentState
from .configuration import PROVIDER_KEY, E2BBackendConfiguration, E2BCredential, E2BProviderConfiguration
from .provider import E2BEnvironment, decode_state, descriptor
from .runtime import E2BProviderRuntime


class E2BEnvironmentProvider(EnvironmentProvider):
    provider_configuration_model = E2BBackendConfiguration
    credential_model = E2BCredential
    supports_stop = True
    supports_destroy = True
    requires_keepalive = True

    @property
    def display_name(self) -> str:
        return "E2B"

    @property
    def key(self) -> str:
        return PROVIDER_KEY

    @property
    def configuration_models(self) -> dict[str, type[BaseModel]]:
        return {"1": E2BProviderConfiguration}

    async def create_runtime(
        self, *, configuration: BaseModel, credential: BaseModel | None, context: ProviderRuntimeContext
    ) -> E2BProviderRuntime:
        if not isinstance(configuration, E2BBackendConfiguration) or not isinstance(credential, E2BCredential):
            raise TypeError("E2B requires E2BBackendConfiguration and E2BCredential")
        return E2BProviderRuntime(
            api_key=credential.api_key,
            domain=configuration.domain,
            api_url=configuration.api_url,
            managed=context.managed,
            operation_id=context.operation_id,
        )

    def describe_configuration(self, configuration: BaseModel) -> EnvironmentDescriptor:
        if not isinstance(configuration, E2BProviderConfiguration):
            raise TypeError("E2B requires E2BProviderConfiguration")
        return descriptor(configuration)

    def target_identity(self, *, configuration: BaseModel, state: EnvironmentState | None) -> str | None:
        if not isinstance(configuration, E2BProviderConfiguration):
            raise TypeError("E2B requires E2BProviderConfiguration")
        data = decode_state(configuration, state)
        return data.sandbox_id if data is not None else None

    def create_environment(
        self,
        *,
        configuration: BaseModel,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: object | None = None,
    ) -> Environment:
        if not isinstance(configuration, E2BProviderConfiguration) or not isinstance(runtime, E2BProviderRuntime):
            raise TypeError("E2B requires E2BProviderConfiguration and E2BProviderRuntime")
        return E2BEnvironment(configuration, environment_id=environment_id, state=state, runtime=runtime)
