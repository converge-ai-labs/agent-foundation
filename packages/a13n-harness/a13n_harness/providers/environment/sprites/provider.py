"""Sprites retains native execution and offers explicitly configured Envd/EIP."""

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.models import EnvironmentDescriptor, EnvironmentState, decode_target_state
from a13n_harness.environment.session_recovery import EnvdSessionRecovery
from a13n_harness.providers.environment._backend import ExecutionBackend
from a13n_harness.providers.environment._backend_factory import BackendFactory
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.eip.binding import configured_descriptor
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime, descriptor
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.sprites.execution import SpritesExecution
from a13n_harness.providers.environment.sprites.http import HttpReference, http_execution
from a13n_harness.providers.environment.sprites.shared import (
    SpritesConnectionConfiguration,
    SpritesEnvironmentConfiguration,
    SpritesTargetState,
)


async def runtime(
    *, configuration: SpritesConnectionConfiguration, credential: TokenCredential | None
) -> NativeRuntime:
    if credential is None:
        raise ValueError("Sprites requires a credential")
    return NativeRuntime(configuration, credential)


def describe(configuration: SpritesEnvironmentConfiguration) -> EnvironmentDescriptor:
    return (
        configured_descriptor(working_directory="/workspace")
        if configuration.execution == "envd_http"
        else descriptor(configuration)
    )


def identity(*, configuration: SpritesEnvironmentConfiguration, state: EnvironmentState | None) -> str:
    value = decode_target_state("sprites", state, SpritesTargetState, fingerprint=configuration.fingerprint)
    if value is None:
        raise failure("sprites", "provider_state_required", Category.INVALID)
    return value.backing_id


def construct(
    *,
    configuration: SpritesEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: NativeRuntime | None,
    session_recovery: EnvdSessionRecovery | None = None,
) -> ExecutionBackend:
    if runtime is None:
        raise TypeError("Sprites requires a runtime")
    if configuration.execution == "native":
        return SpritesExecution(configuration, environment_id, state, runtime)
    return http_execution(
        HttpReference(configuration, environment_id, state, runtime), session_recovery=session_recovery
    )


_factory = BackendFactory(
    key="sprites",
    environment_model=SpritesEnvironmentConfiguration,
    execution=construct,
    describe=describe,
    runtime_factory=runtime,
    target_identity=identity,
)
SPRITES = EnvironmentProviderDefinition(
    type="sprites",
    display_name="Fly.io Sprites",
    configuration_model=SpritesConnectionConfiguration,
    credential_model=TokenCredential,
    environment_model=SpritesEnvironmentConfiguration,
    connector_factory=_factory.connector,
    describe_environment=describe,
    target_identity=identity,
)
