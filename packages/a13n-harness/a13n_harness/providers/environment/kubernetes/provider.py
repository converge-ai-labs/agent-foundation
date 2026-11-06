"""Fixed-target Kubernetes connector composed with shared EIP execution."""

from __future__ import annotations

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import provider_error
from a13n_harness.environment.models import EnvironmentDescriptor, EnvironmentState
from a13n_harness.environment.session_recovery import EnvdSessionBorrower
from a13n_harness.providers.environment._backend_factory import BackendFactory
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.eip.binding import configured_descriptor

from .configuration import KEY, KubernetesConnectionConfiguration, KubernetesEnvironmentConfiguration, decode
from .http import execution
from .runtime import KubernetesRuntime, open_runtime


def describe(configuration: KubernetesEnvironmentConfiguration) -> EnvironmentDescriptor:
    return configured_descriptor(working_directory=configuration.working_directory)


def identity(*, configuration: KubernetesEnvironmentConfiguration, state: EnvironmentState | None) -> str:
    target = decode(configuration, state)
    if target is None or target.pod_uid is None:
        raise provider_error(KEY, "provider_state_required", Category.INVALID)
    return target.pod_uid


def construct(
    *,
    configuration: KubernetesEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: KubernetesRuntime | None,
    session_recovery: EnvdSessionBorrower | None = None,
):
    target = decode(configuration, state)
    if target is None or runtime is None:
        raise provider_error(KEY, "provider_state_required", Category.INVALID)
    runtime.validate(target)
    return execution(runtime, target, configuration, environment_id, session_recovery=session_recovery)


_factory = BackendFactory(
    key=KEY,
    environment_model=KubernetesEnvironmentConfiguration,
    execution=construct,
    describe=describe,
    runtime_factory=open_runtime,
    target_identity=identity,
)

KUBERNETES = EnvironmentProviderDefinition(
    type=KEY,
    display_name="Kubernetes",
    configuration_model=KubernetesConnectionConfiguration,
    environment_model=KubernetesEnvironmentConfiguration,
    connector_factory=_factory.connector,
    describe_environment=describe,
    target_identity=identity,
)
