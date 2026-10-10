"""Runloop provider implementation."""

from ..native.configuration import TargetState, TokenCredential
from ..native.environment import native_definition
from .execution import RunloopExecution
from .management import RunloopManagement
from .shared import RunloopConnectionConfiguration as RunloopConnectionConfiguration
from .shared import RunloopEnvironmentConfiguration as RunloopEnvironmentConfiguration

RUNLOOP = native_definition(
    type="runloop",
    display_name="Runloop",
    configuration_model=RunloopConnectionConfiguration,
    credential_model=TokenCredential,
    environment_model=RunloopEnvironmentConfiguration,
    state_model=TargetState,
    management_type=RunloopManagement,
    execution_type=RunloopExecution,
    supports_stop=True,
    requires_keepalive=True,
)
