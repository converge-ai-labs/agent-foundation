from dataclasses import replace

from a13n_harness.providers.environment.daytona.provider import DAYTONA as EXECUTION
from a13n_harness.providers.environment.daytona.shared import DaytonaConnectionConfiguration, resolved_runtime
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime

from a13n_service.providers.environment._backend_factory import ManagementBackendFactory
from a13n_service.providers.environment.daytona_build import DaytonaDesktopPreparation
from a13n_service.providers.environment.definition import management_definition

from . import desktop_build
from .configuration import DaytonaRecipe
from .management import DaytonaManagement


async def runtime(
    *, configuration: DaytonaConnectionConfiguration, credential: TokenCredential | None
) -> NativeRuntime:
    if credential is None:
        raise ValueError("Daytona requires a credential")
    return await resolved_runtime(configuration, credential)


_factory = ManagementBackendFactory(
    key="daytona", environment_model=DaytonaRecipe, management=DaytonaManagement, runtime_factory=runtime
)
DAYTONA = management_definition(
    EXECUTION,
    environment_model=DaytonaRecipe,
    provider_factory=_factory.provider,
    supports_stop=True,
    supports_destroy=True,
    requires_keepalive=False,
)


DAYTONA = replace(
    DAYTONA,
    daytona_desktop_preparation=DaytonaDesktopPreparation(
        specification=desktop_build.specification,
        revision=desktop_build.revision,
        connect=desktop_build.DesktopBuildAPI,
    ),
)
