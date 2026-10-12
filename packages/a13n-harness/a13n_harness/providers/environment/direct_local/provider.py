"""DirectLocal provider implementation."""

from __future__ import annotations

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory
from a13n_harness.environment.models import EnvironmentDescriptor, EnvironmentState
from a13n_harness.providers.environment._backend import ExecutionBackend
from a13n_harness.providers.environment._backend_factory import BackendFactory
from a13n_harness.providers.environment.configuration import EnvironmentProviderConfiguration
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.direct_local.configuration import DirectLocalEnvironmentConfiguration
from a13n_harness.providers.environment.direct_local.execution import DirectLocalExecution
from a13n_harness.providers.environment.direct_local.shared import _PROVIDER_KEY, _descriptor, _provider_error


def _describe(configuration: DirectLocalEnvironmentConfiguration) -> EnvironmentDescriptor:
    if not isinstance(configuration, DirectLocalEnvironmentConfiguration):
        raise TypeError("Unexpected Provider recipe")
    return _descriptor(configuration, "unprepared")


def _identity(*, configuration: DirectLocalEnvironmentConfiguration, state: EnvironmentState | None) -> str | None:
    if not isinstance(configuration, DirectLocalEnvironmentConfiguration) or state is not None:
        raise _provider_error(
            "Direct Local requires a valid stateless workspace configuration.",
            code="provider_state_invalid",
            category=EnvironmentProviderErrorCategory.INVALID,
        )
    return str(configuration.root.path)


def _construct_execution(
    *,
    configuration: DirectLocalEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: object | None,
) -> ExecutionBackend:
    """Direct Local needs no collaborator: the host filesystem is the target."""
    del runtime
    if state is not None:
        raise _provider_error(
            "Direct Local is stateless and does not accept Environment state.",
            code="provider_state_invalid",
            category=EnvironmentProviderErrorCategory.INVALID,
        )
    return DirectLocalExecution(configuration, environment_id=environment_id)


_factory = BackendFactory(
    key=_PROVIDER_KEY,
    environment_model=DirectLocalEnvironmentConfiguration,
    execution=_construct_execution,
    describe=_describe,
    target_identity=_identity,
)

DIRECT_LOCAL = EnvironmentProviderDefinition(
    type="direct_local",
    display_name="Direct Local",
    configuration_model=EnvironmentProviderConfiguration,
    environment_model=DirectLocalEnvironmentConfiguration,
    connector_factory=_factory.connector,
    describe_environment=_describe,
    target_identity=_identity,
)
