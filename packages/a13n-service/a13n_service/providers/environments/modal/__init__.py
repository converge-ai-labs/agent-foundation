from a13n_harness.providers.environment.modal.provider import MODAL as EXECUTION

from a13n_service.providers.environment.native import native_management_definition

from .management import ModalManagement

MODAL = native_management_definition(
    EXECUTION, management_type=ModalManagement, supports_stop=True, requires_keepalive=True
)
