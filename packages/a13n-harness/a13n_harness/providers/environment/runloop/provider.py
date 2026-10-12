"""Runloop provider implementation."""

from a13n_harness.providers.environment.native.configuration import TargetState, TokenCredential
from a13n_harness.providers.environment.native.environment import native_definition
from a13n_harness.providers.environment.runloop.execution import RunloopExecution
from a13n_harness.providers.environment.runloop.shared import (
    RunloopConnectionConfiguration as RunloopConnectionConfiguration,
)
from a13n_harness.providers.environment.runloop.shared import (
    RunloopEnvironmentConfiguration as RunloopEnvironmentConfiguration,
)

RUNLOOP = native_definition(
    type="runloop",
    display_name="Runloop",
    configuration_model=RunloopConnectionConfiguration,
    credential_model=TokenCredential,
    environment_model=RunloopEnvironmentConfiguration,
    state_model=TargetState,
    execution_type=RunloopExecution,
)
