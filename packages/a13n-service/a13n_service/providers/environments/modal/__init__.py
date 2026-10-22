from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.modal.diagnostics import desktop_operation
from a13n_harness.providers.environment.modal.provider import MODAL as EXECUTION
from a13n_harness.providers.environment.modal.shared import ModalConnectionConfiguration, ModalCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime

from a13n_service.providers.environment._backend_factory import BackendProvider, ManagementBackendFactory
from a13n_service.providers.environment.definition import management_definition

from .configuration import ModalRecipe
from .management import ModalManagement


async def build_runtime(
    *, configuration: ModalConnectionConfiguration, credential: ModalCredential | None
) -> NativeRuntime:
    if credential is None:
        raise ValueError("Modal requires a credential")
    return NativeRuntime(configuration, credential)


_factory = ManagementBackendFactory(
    key="modal", environment_model=ModalRecipe, management=ModalManagement, runtime_factory=build_runtime
)


class ModalProvider(BackendProvider[ModalConnectionConfiguration, ModalCredential, ModalRecipe, NativeRuntime]):
    @asynccontextmanager
    async def desktop_session(
        self, environment: object, *, environment_id: str, state: EnvironmentState | None, operation_id: str
    ) -> AsyncGenerator[ModalManagement]:
        """Own one fixed-target connection for a single desktop transition request."""
        # Retain the existing validation, error translation and shielded cleanup.
        with desktop_operation(operation_id, "session"):
            async with self._operation(environment, environment_id, state, operation_id) as target:
                assert isinstance(target, ModalManagement)
                target.reuse_transport = True
                try:
                    yield target
                except BaseException:
                    target.discard_transport()
                    raise


async def provider(
    *, configuration: ModalConnectionConfiguration, credential: ModalCredential | None, runtime: NativeRuntime | None
) -> ModalProvider:
    if runtime is None:
        runtime = await build_runtime(configuration=configuration, credential=credential)
    return ModalProvider(_factory, runtime, None)


MODAL = management_definition(
    EXECUTION,
    environment_model=ModalRecipe,
    provider_factory=provider,
    supports_stop=True,
    supports_destroy=True,
    requires_keepalive=True,
)
