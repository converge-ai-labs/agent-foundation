"""E2B provider implementation."""

from __future__ import annotations

from pydantic import BaseModel

from .._backend import ExecutionBackend, ManagementBackend
from .._backend_factory import BackendFactory
from ..authentication import Authentication, CredentialMode
from ..definition import EnvironmentProviderDefinition
from ..errors import EnvironmentProviderErrorCategory as Category
from ..models import (
    EnvironmentDescriptor,
    EnvironmentState,
    decode_target_state,
)
from .configuration import (
    PROVIDER_KEY,
    E2BConnectionConfiguration,
    E2BCredential,
    E2BEnvironmentConfiguration,
    E2BProviderStateData,
)
from .errors import provider_error
from .execution import E2BExecution
from .management import E2BManagement
from .shared import E2BProviderRuntime as E2BProviderRuntime
from .shared import descriptor


async def _runtime(*, configuration: BaseModel, credential: BaseModel | None) -> E2BProviderRuntime:
    if not isinstance(configuration, E2BConnectionConfiguration) or not isinstance(credential, E2BCredential):
        raise TypeError("E2B requires E2BConnectionConfiguration and E2BCredential")
    return E2BProviderRuntime(
        api_key=credential.api_key,
        domain=configuration.domain,
        api_url=configuration.api_url,
    )


def _describe(configuration: E2BEnvironmentConfiguration) -> EnvironmentDescriptor:
    if not isinstance(configuration, E2BEnvironmentConfiguration):
        raise TypeError("E2B requires E2BEnvironmentConfiguration")
    return descriptor(configuration)


def _identity(*, configuration: E2BEnvironmentConfiguration, state: EnvironmentState | None) -> str | None:
    if not isinstance(configuration, E2BEnvironmentConfiguration):
        raise TypeError("E2B requires E2BEnvironmentConfiguration")
    data = decode_target_state(PROVIDER_KEY, state, E2BProviderStateData, fingerprint=configuration.fingerprint)
    if data is None:
        raise provider_error("provider_state_required", Category.INVALID)
    return data.sandbox_id


def _construct_management(
    *,
    configuration: E2BEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: E2BProviderRuntime | None,
    operation_id: str,
) -> ManagementBackend:
    if not isinstance(configuration, E2BEnvironmentConfiguration) or runtime is None:
        raise TypeError("E2B requires E2BEnvironmentConfiguration and E2BProviderRuntime")
    return E2BManagement(
        configuration,
        environment_id=environment_id,
        state=state,
        runtime=runtime,
        operation_id=operation_id,
    )


def _construct_execution(
    *,
    configuration: E2BEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: E2BProviderRuntime | None,
) -> ExecutionBackend:
    if not isinstance(configuration, E2BEnvironmentConfiguration) or runtime is None:
        raise TypeError("E2B requires E2BEnvironmentConfiguration and E2BProviderRuntime")
    return E2BExecution(
        configuration,
        environment_id=environment_id,
        state=state,
        runtime=runtime,
    )


_factory = BackendFactory(
    key=PROVIDER_KEY,
    environment_model=E2BEnvironmentConfiguration,
    management=_construct_management,
    execution=_construct_execution,
    describe=_describe,
    runtime_factory=_runtime,
    target_identity=_identity,
)

E2B = EnvironmentProviderDefinition(
    type=PROVIDER_KEY,
    display_name="E2B",
    configuration_model=E2BConnectionConfiguration,
    credential_model=E2BCredential,
    environment_model=E2BEnvironmentConfiguration,
    connector_factory=_factory.connector,
    provider_factory=_factory.provider,
    describe_environment=_describe,
    target_identity=_identity,
    authentication=Authentication(mode=CredentialMode.required),
    setup_url="https://e2b.dev/dashboard?tab=keys",
    setup_label="E2B API keys",
    supports_stop=True,
    supports_destroy=True,
    requires_keepalive=True,
)
