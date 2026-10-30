from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.sprites.provider import SPRITES as EXECUTION
from a13n_harness.providers.environment.sprites.shared import SpritesConnectionConfiguration

from a13n_service.providers.environment._backend_factory import ManagementBackendFactory
from a13n_service.providers.environment.definition import management_definition

from .configuration import SpritesRecipe
from .management import SpritesManagement


async def runtime(
    *, configuration: SpritesConnectionConfiguration, credential: TokenCredential | None
) -> NativeRuntime:
    if credential is None:
        raise ValueError("Sprites requires a credential")
    return NativeRuntime(configuration, credential)


_factory = ManagementBackendFactory(
    key="sprites", environment_model=SpritesRecipe, management=SpritesManagement, runtime_factory=runtime
)
SPRITES = management_definition(
    EXECUTION,
    environment_model=SpritesRecipe,
    provider_factory=_factory.provider,
    supports_stop=False,
    supports_destroy=True,
    requires_keepalive=False,
)
