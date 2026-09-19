"""Built-in Environment definitions, using the same contract as installed plugins."""

from collections.abc import Iterable

from .daytona.provider import DAYTONA
from .definition import EnvironmentProviderDefinition
from .direct_local.provider import DIRECT_LOCAL
from .docker.provider import DOCKER
from .e2b.provider import E2B
from .local_envd.provider import LOCAL_ENVD
from .modal.provider import MODAL
from .remote_envd.http import HTTP_ENVD
from .remote_envd.websocket import WEBSOCKET_ENVD
from .runloop.provider import RUNLOOP
from .sprites.provider import SPRITES
from .vercel.provider import VERCEL

BUILT_IN_ENVIRONMENT_PROVIDERS: tuple[EnvironmentProviderDefinition, ...] = (
    DIRECT_LOCAL,
    LOCAL_ENVD,
    DOCKER,
    E2B,
    DAYTONA,
    MODAL,
    VERCEL,
    SPRITES,
    RUNLOOP,
    HTTP_ENVD,
    WEBSOCKET_ENVD,
)


def select_builtin_environment_providers(types: Iterable[str]) -> tuple[EnvironmentProviderDefinition, ...]:
    """Select native definitions without constructing any target or vendor client."""
    definitions = {definition.type: definition for definition in BUILT_IN_ENVIRONMENT_PROVIDERS}
    return tuple(definitions[key] for key in types)
