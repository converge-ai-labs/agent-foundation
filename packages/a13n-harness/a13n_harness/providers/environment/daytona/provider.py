"""Daytona resources expose Agent operations exclusively through Envd/EIP."""

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.models import EnvironmentDescriptor, EnvironmentState, decode_target_state
from a13n_harness.environment.session_recovery import EnvdSessionRecovery
from a13n_harness.providers.environment._backend_factory import BackendFactory
from a13n_harness.providers.environment.daytona.http import HttpReference, http_execution
from a13n_harness.providers.environment.daytona.shared import (
    DaytonaConnectionConfiguration,
    DaytonaEnvironmentConfiguration,
)
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.eip.binding import configured_descriptor
from a13n_harness.providers.environment.native.configuration import TargetState, TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.remote_envd.environment import RemoteEnvdExecution


async def runtime(
    *, configuration: DaytonaConnectionConfiguration, credential: TokenCredential | None
) -> NativeRuntime:
    if credential is None:
        raise ValueError("Daytona requires a credential")
    return NativeRuntime(configuration, credential)


def describe(configuration: DaytonaEnvironmentConfiguration) -> EnvironmentDescriptor:
    return configured_descriptor(working_directory="/workspace")


def identity(*, configuration: DaytonaEnvironmentConfiguration, state: EnvironmentState | None) -> str:
    value = decode_target_state("daytona", state, TargetState, fingerprint=configuration.fingerprint)
    if value is None:
        raise failure("daytona", "provider_state_required", Category.INVALID)
    return value.target_id


def construct(
    *,
    configuration: DaytonaEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: NativeRuntime | None,
    session_recovery: EnvdSessionRecovery | None = None,
) -> RemoteEnvdExecution:
    if runtime is None:
        raise TypeError("Daytona requires a runtime")
    return http_execution(
        HttpReference(configuration, environment_id, state, runtime), session_recovery=session_recovery
    )


_factory = BackendFactory(
    key="daytona",
    environment_model=DaytonaEnvironmentConfiguration,
    execution=construct,
    describe=describe,
    runtime_factory=runtime,
    target_identity=identity,
)
DAYTONA = EnvironmentProviderDefinition(
    type="daytona",
    display_name="Daytona",
    configuration_model=DaytonaConnectionConfiguration,
    credential_model=TokenCredential,
    environment_model=DaytonaEnvironmentConfiguration,
    connector_factory=_factory.connector,
    describe_environment=describe,
    target_identity=identity,
)
