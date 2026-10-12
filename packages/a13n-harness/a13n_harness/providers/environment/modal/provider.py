"""Modal provider implementation."""

from __future__ import annotations

from a13n_harness.providers.environment.modal.execution import ModalExecution
from a13n_harness.providers.environment.modal.shared import ModalConnectionConfiguration as ModalConnectionConfiguration
from a13n_harness.providers.environment.modal.shared import ModalCredential as ModalCredential
from a13n_harness.providers.environment.modal.shared import (
    ModalEnvironmentConfiguration as ModalEnvironmentConfiguration,
)
from a13n_harness.providers.environment.modal.shared import ModalState
from a13n_harness.providers.environment.native.environment import native_definition

MODAL = native_definition(
    type="modal",
    display_name="Modal",
    configuration_model=ModalConnectionConfiguration,
    credential_model=ModalCredential,
    environment_model=ModalEnvironmentConfiguration,
    state_model=ModalState,
    execution_type=ModalExecution,
)
