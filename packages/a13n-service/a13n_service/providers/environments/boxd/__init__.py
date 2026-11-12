from a13n_harness.providers.environment.boxd.provider import BOXD as EXECUTION

from a13n_service.providers.environment.native import native_management_definition

from .management import BoxdManagement

BOXD = native_management_definition(
    EXECUTION, management_type=BoxdManagement, supports_stop=True, requires_keepalive=False
)
