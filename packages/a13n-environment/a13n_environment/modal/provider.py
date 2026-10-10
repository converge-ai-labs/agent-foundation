"""Modal provider implementation."""

from __future__ import annotations

from ..native.environment import native_definition
from .execution import ModalExecution
from .management import ModalManagement
from .shared import ModalConnectionConfiguration as ModalConnectionConfiguration
from .shared import ModalCredential as ModalCredential
from .shared import ModalEnvironmentConfiguration as ModalEnvironmentConfiguration
from .shared import ModalState

MODAL = native_definition(
    type="modal",
    display_name="Modal",
    configuration_model=ModalConnectionConfiguration,
    credential_model=ModalCredential,
    environment_model=ModalEnvironmentConfiguration,
    state_model=ModalState,
    management_type=ModalManagement,
    execution_type=ModalExecution,
    supports_stop=True,
    requires_keepalive=True,
)
