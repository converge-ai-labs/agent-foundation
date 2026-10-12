from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.docker.provider import DOCKER as EXECUTION
from a13n_harness.providers.environment.docker.provider import _runtime
from a13n_harness.providers.environment.docker.runtime import DockerProviderRuntime

from a13n_service.providers.environment._backend import ManagementBackend
from a13n_service.providers.environment._backend_factory import ManagementBackendFactory
from a13n_service.providers.environment.definition import management_definition

from .configuration import DockerRecipe
from .management import DockerManagement


def _construct_management(
    *,
    configuration: DockerRecipe,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: DockerProviderRuntime | None,
    operation_id: str,
) -> ManagementBackend:
    del operation_id
    if not isinstance(configuration, DockerRecipe) or runtime is None:
        raise TypeError("Docker requires typed configuration and runtime")
    return DockerManagement(configuration, environment_id, state, runtime)


_factory = ManagementBackendFactory(
    key="docker", environment_model=DockerRecipe, management=_construct_management, runtime_factory=_runtime
)
DOCKER = management_definition(
    EXECUTION,
    environment_model=DockerRecipe,
    provider_factory=_factory.provider,
    supports_stop=True,
    supports_destroy=True,
    requires_keepalive=False,
)
