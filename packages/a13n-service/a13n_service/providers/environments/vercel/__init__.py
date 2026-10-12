from a13n_harness.providers.environment.vercel.provider import VERCEL as EXECUTION

from a13n_service.providers.environment.native import native_management_definition

from .management import VercelManagement

VERCEL = native_management_definition(
    EXECUTION, management_type=VercelManagement, supports_stop=True, requires_keepalive=True
)
