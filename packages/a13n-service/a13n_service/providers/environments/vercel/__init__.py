from dataclasses import replace

from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.vercel.provider import VERCEL as EXECUTION
from a13n_harness.providers.environment.vercel.shared import VercelConnectionConfiguration

from a13n_service.providers.environment._backend_factory import ManagementBackendFactory
from a13n_service.providers.environment.definition import management_definition
from a13n_service.providers.environment.vercel_build import VercelDesktopPreparation

from .configuration import VercelRecipe
from .desktop_build import DesktopBuildAPI, revision, specification
from .management import VercelManagement


async def runtime(*, configuration: VercelConnectionConfiguration, credential: TokenCredential | None) -> NativeRuntime:
    if credential is None:
        raise ValueError("Vercel requires a credential")
    return NativeRuntime(configuration, credential)


_factory = ManagementBackendFactory(
    key="vercel", environment_model=VercelRecipe, management=VercelManagement, runtime_factory=runtime
)
VERCEL = management_definition(
    EXECUTION,
    environment_model=VercelRecipe,
    provider_factory=_factory.provider,
    supports_stop=True,
    supports_destroy=True,
    requires_keepalive=True,
)

VERCEL = replace(VERCEL, vercel_desktop_preparation=VercelDesktopPreparation(specification, revision, DesktopBuildAPI))
