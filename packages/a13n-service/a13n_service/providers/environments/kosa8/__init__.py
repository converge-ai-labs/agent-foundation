from a13n_harness.providers.environment.kosa8.provider import KOSA8 as EXECUTION

from a13n_service.providers.environment.native import native_management_definition

from .management import Kosa8Management

KOSA8 = native_management_definition(
    EXECUTION, management_type=Kosa8Management, supports_stop=True, requires_keepalive=False
)
