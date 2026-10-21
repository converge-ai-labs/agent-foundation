"""Modal resources expose Agent operations exclusively through Envd/EIP."""

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.models import EnvironmentDescriptor, EnvironmentState, decode_target_state
from a13n_harness.environment.session_recovery import EnvdSessionRecovery
from a13n_harness.providers.environment._backend_factory import BackendFactory
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.eip.binding import configured_descriptor
from a13n_harness.providers.environment.modal.http import HttpReference, http_execution
from a13n_harness.providers.environment.modal.shared import (
    ModalConnectionConfiguration,
    ModalCredential,
    ModalEnvironmentConfiguration,
    ModalState,
)
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.remote_envd.environment import RemoteEnvdExecution


async def runtime(*, configuration: ModalConnectionConfiguration, credential: ModalCredential | None) -> NativeRuntime:
    if credential is None:
        raise ValueError("Modal requires a credential")
    return NativeRuntime(configuration, credential)


def describe(configuration: ModalEnvironmentConfiguration) -> EnvironmentDescriptor:
    return configured_descriptor(working_directory="/workspace")


def identity(*, configuration: ModalEnvironmentConfiguration, state: EnvironmentState | None) -> str:
    value = decode_target_state("modal", state, ModalState, fingerprint=configuration.fingerprint)
    if value is None:
        raise failure("modal", "provider_state_required", Category.INVALID)
    return value.target_id


def construct(
    *,
    configuration: ModalEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: NativeRuntime | None,
    session_recovery: EnvdSessionRecovery | None = None,
) -> RemoteEnvdExecution:
    if runtime is None:
        raise TypeError("Modal requires a runtime")
    return http_execution(
        HttpReference(configuration, environment_id, state, runtime), session_recovery=session_recovery
    )


_factory = BackendFactory(
    key="modal",
    environment_model=ModalEnvironmentConfiguration,
    execution=construct,
    describe=describe,
    runtime_factory=runtime,
    target_identity=identity,
)
MODAL = EnvironmentProviderDefinition(
    type="modal",
    display_name="Modal",
    configuration_model=ModalConnectionConfiguration,
    credential_model=ModalCredential,
    environment_model=ModalEnvironmentConfiguration,
    connector_factory=_factory.connector,
    describe_environment=describe,
    target_identity=identity,
)
