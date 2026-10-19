from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.e2b.provider import E2B as EXECUTION
from a13n_harness.providers.environment.e2b.provider import _runtime
from a13n_harness.providers.environment.e2b.shared import E2BProviderRuntime

from a13n_service.providers.environment._backend import ManagementBackend
from a13n_service.providers.environment._backend_factory import ManagementBackendFactory
from a13n_service.providers.environment.definition import management_definition

from .configuration import E2BRecipe
from .management import E2BManagement


def _construct_management(
    *,
    configuration: E2BRecipe,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: E2BProviderRuntime | None,
    operation_id: str,
) -> ManagementBackend:
    if not isinstance(configuration, E2BRecipe) or runtime is None:
        raise TypeError("E2B requires E2BRecipe and E2BProviderRuntime")
    return E2BManagement(
        configuration,
        environment_id=environment_id,
        state=state,
        runtime=runtime,
        operation_id=operation_id,
    )


_factory = ManagementBackendFactory(
    key="e2b", environment_model=E2BRecipe, management=_construct_management, runtime_factory=_runtime
)
E2B = management_definition(
    EXECUTION,
    environment_model=E2BRecipe,
    provider_factory=_factory.provider,
    supports_stop=True,
    supports_destroy=True,
    requires_keepalive=True,
    metrics_launch=lambda recipe: recipe.execution == "envd_http",
)
