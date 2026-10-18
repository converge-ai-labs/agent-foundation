"""Daytona provider implementation."""

from a13n_harness.environment.models import EnvironmentDescriptor
from a13n_harness.providers.environment.daytona.computer import with_computer
from a13n_harness.providers.environment.daytona.execution import DaytonaExecution
from a13n_harness.providers.environment.daytona.shared import (
    DaytonaConnectionConfiguration as DaytonaConnectionConfiguration,
)
from a13n_harness.providers.environment.daytona.shared import (
    DaytonaEnvironmentConfiguration as DaytonaEnvironmentConfiguration,
)
from a13n_harness.providers.environment.native.configuration import TargetState, TokenCredential
from a13n_harness.providers.environment.native.environment import descriptor, native_definition


def describe(configuration: DaytonaEnvironmentConfiguration) -> EnvironmentDescriptor:
    value = descriptor(configuration)
    return with_computer(value) if configuration.desktop else value


DAYTONA = native_definition(
    type="daytona",
    display_name="Daytona",
    configuration_model=DaytonaConnectionConfiguration,
    credential_model=TokenCredential,
    environment_model=DaytonaEnvironmentConfiguration,
    state_model=TargetState,
    execution_type=DaytonaExecution,
    describe_environment=describe,
)
