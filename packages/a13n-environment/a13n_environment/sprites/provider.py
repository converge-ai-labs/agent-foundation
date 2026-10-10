"""Sprites provider implementation."""

from ..native.configuration import NamedTargetState, TokenCredential
from ..native.environment import native_definition
from .execution import SpritesExecution
from .management import SpritesManagement
from .shared import SpritesConnectionConfiguration as SpritesConnectionConfiguration
from .shared import SpritesEnvironmentConfiguration as SpritesEnvironmentConfiguration

SPRITES = native_definition(
    type="sprites",
    display_name="Fly.io Sprites",
    configuration_model=SpritesConnectionConfiguration,
    credential_model=TokenCredential,
    environment_model=SpritesEnvironmentConfiguration,
    state_model=NamedTargetState,
    management_type=SpritesManagement,
    execution_type=SpritesExecution,
    supports_stop=False,
    requires_keepalive=False,
)
