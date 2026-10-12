from a13n_harness.providers.environment.runloop.provider import RUNLOOP as EXECUTION

from a13n_service.providers.environment.native import native_management_definition

from .management import RunloopManagement

RUNLOOP = native_management_definition(
    EXECUTION, management_type=RunloopManagement, supports_stop=True, requires_keepalive=True
)
