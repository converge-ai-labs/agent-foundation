"""Built-in Environment definitions, using the same contract as installed plugins."""

from collections.abc import Iterable

from a13n_harness.providers.environment.daytona.provider import DAYTONA
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL
from a13n_harness.providers.environment.docker.provider import DOCKER
from a13n_harness.providers.environment.e2b.provider import E2B
from a13n_harness.providers.environment.local_envd.provider import LOCAL_ENVD
from a13n_harness.providers.environment.modal.provider import MODAL
from a13n_harness.providers.environment.remote_envd.http import HTTP_ENVD
from a13n_harness.providers.environment.remote_envd.websocket import WEBSOCKET_ENVD
from a13n_harness.providers.environment.runloop.provider import RUNLOOP
from a13n_harness.providers.environment.sprites.provider import SPRITES
from a13n_harness.providers.environment.vercel.provider import VERCEL

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
