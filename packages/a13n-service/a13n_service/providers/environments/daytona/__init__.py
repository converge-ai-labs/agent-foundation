from a13n_harness.providers.environment.daytona.provider import DAYTONA as EXECUTION

from a13n_service.providers.environment.native import native_management_definition

from .management import DaytonaManagement

DAYTONA = native_management_definition(
    EXECUTION, management_type=DaytonaManagement, supports_stop=True, requires_keepalive=False
)
