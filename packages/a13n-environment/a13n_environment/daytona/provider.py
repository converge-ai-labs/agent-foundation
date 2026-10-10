"""Daytona provider implementation."""

from ..native.configuration import TargetState, TokenCredential
from ..native.environment import native_definition
from .execution import DaytonaExecution
from .management import DaytonaManagement
from .shared import DaytonaConnectionConfiguration as DaytonaConnectionConfiguration
from .shared import DaytonaEnvironmentConfiguration as DaytonaEnvironmentConfiguration

DAYTONA = native_definition(
    type="daytona",
    display_name="Daytona",
    configuration_model=DaytonaConnectionConfiguration,
    credential_model=TokenCredential,
    environment_model=DaytonaEnvironmentConfiguration,
    state_model=TargetState,
    management_type=DaytonaManagement,
    execution_type=DaytonaExecution,
    supports_stop=True,
    requires_keepalive=False,
)
