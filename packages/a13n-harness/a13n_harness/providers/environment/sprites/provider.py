"""Sprites provider implementation."""

from a13n_harness.providers.environment.native.configuration import NamedTargetState, TokenCredential
from a13n_harness.providers.environment.native.environment import native_definition
from a13n_harness.providers.environment.sprites.execution import SpritesExecution
from a13n_harness.providers.environment.sprites.shared import (
    SpritesConnectionConfiguration as SpritesConnectionConfiguration,
)
from a13n_harness.providers.environment.sprites.shared import (
    SpritesEnvironmentConfiguration as SpritesEnvironmentConfiguration,
)

SPRITES = native_definition(
    type="sprites",
    display_name="Fly.io Sprites",
    configuration_model=SpritesConnectionConfiguration,
    credential_model=TokenCredential,
    environment_model=SpritesEnvironmentConfiguration,
    state_model=NamedTargetState,
    execution_type=SpritesExecution,
)
