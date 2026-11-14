"""boxd provider implementation."""

from a13n_harness.providers.environment.boxd.execution import BoxdExecution
from a13n_harness.providers.environment.boxd.shared import (
    BoxdConnectionConfiguration as BoxdConnectionConfiguration,
)
from a13n_harness.providers.environment.boxd.shared import (
    BoxdEnvironmentConfiguration as BoxdEnvironmentConfiguration,
)
from a13n_harness.providers.environment.native.configuration import TargetState, TokenCredential
from a13n_harness.providers.environment.native.environment import native_definition

BOXD = native_definition(
    type="boxd",
    display_name="boxd",
    configuration_model=BoxdConnectionConfiguration,
    credential_model=TokenCredential,
    environment_model=BoxdEnvironmentConfiguration,
    state_model=TargetState,
    execution_type=BoxdExecution,
)
