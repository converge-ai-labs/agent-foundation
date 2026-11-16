"""kosa8 provider implementation."""

from a13n_harness.providers.environment.kosa8.execution import Kosa8Execution
from a13n_harness.providers.environment.kosa8.shared import (
    Kosa8ConnectionConfiguration as Kosa8ConnectionConfiguration,
)
from a13n_harness.providers.environment.kosa8.shared import (
    Kosa8EnvironmentConfiguration as Kosa8EnvironmentConfiguration,
)
from a13n_harness.providers.environment.native.configuration import TargetState, TokenCredential
from a13n_harness.providers.environment.native.environment import native_definition

KOSA8 = native_definition(
    type="kosa8",
    display_name="kosa8",
    configuration_model=Kosa8ConnectionConfiguration,
    credential_model=TokenCredential,
    environment_model=Kosa8EnvironmentConfiguration,
    state_model=TargetState,
    execution_type=Kosa8Execution,
)
