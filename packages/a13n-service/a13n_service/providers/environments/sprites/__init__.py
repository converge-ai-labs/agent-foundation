from a13n_harness.providers.environment.sprites.provider import SPRITES as EXECUTION

from a13n_service.providers.environment.native import native_management_definition

from .management import SpritesManagement

SPRITES = native_management_definition(
    EXECUTION, management_type=SpritesManagement, supports_stop=False, requires_keepalive=False
)
