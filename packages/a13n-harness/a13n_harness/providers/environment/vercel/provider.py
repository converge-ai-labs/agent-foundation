"""Vercel retains native execution and offers explicitly configured Envd/EIP."""

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.models import EnvironmentDescriptor, EnvironmentState, decode_target_state
from a13n_harness.environment.session_recovery import EnvdSessionRecovery
from a13n_harness.providers.environment._backend import ExecutionBackend
from a13n_harness.providers.environment._backend_factory import BackendFactory
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.eip.binding import configured_descriptor
from a13n_harness.providers.environment.native.configuration import NamedTargetState, TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime, descriptor
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.vercel.execution import VercelExecution
from a13n_harness.providers.environment.vercel.http import HttpReference, http_execution
from a13n_harness.providers.environment.vercel.shared import (
    VercelConnectionConfiguration,
    VercelEnvironmentConfiguration,
)


async def runtime(*, configuration: VercelConnectionConfiguration, credential: TokenCredential | None) -> NativeRuntime:
    if credential is None:
        raise ValueError("Vercel requires a credential")
    return NativeRuntime(configuration, credential)


def describe(configuration: VercelEnvironmentConfiguration) -> EnvironmentDescriptor:
    return (
        configured_descriptor(working_directory="/workspace")
        if configuration.execution == "envd_http"
        else descriptor(configuration)
    )


def identity(*, configuration: VercelEnvironmentConfiguration, state: EnvironmentState | None) -> str:
    value = decode_target_state("vercel", state, NamedTargetState, fingerprint=configuration.fingerprint)
    if value is None:
        raise failure("vercel", "provider_state_required", Category.INVALID)
    return value.backing_id


def construct(
    *,
    configuration: VercelEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: NativeRuntime | None,
    session_recovery: EnvdSessionRecovery | None = None,
) -> ExecutionBackend:
    if configuration.snapshot == "auto":
        raise failure("vercel", "desktop_snapshot_not_prepared", Category.INVALID)
    if runtime is None:
        raise TypeError("Vercel requires a runtime")
    if configuration.execution == "native":
        return VercelExecution(configuration, environment_id, state, runtime)
    return http_execution(
        HttpReference(configuration, environment_id, state, runtime), session_recovery=session_recovery
    )


_factory = BackendFactory(
    key="vercel",
    environment_model=VercelEnvironmentConfiguration,
    execution=construct,
    describe=describe,
    runtime_factory=runtime,
    target_identity=identity,
)
VERCEL = EnvironmentProviderDefinition(
    type="vercel",
    display_name="Vercel",
    configuration_model=VercelConnectionConfiguration,
    credential_model=TokenCredential,
    environment_model=VercelEnvironmentConfiguration,
    connector_factory=_factory.connector,
    describe_environment=describe,
    target_identity=identity,
)
