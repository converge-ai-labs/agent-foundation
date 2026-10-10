"""Vercel provider implementation."""

from ..native.configuration import NamedTargetState, TokenCredential
from ..native.environment import native_definition
from .execution import VercelExecution
from .management import VercelManagement
from .shared import VercelConnectionConfiguration as VercelConnectionConfiguration
from .shared import VercelEnvironmentConfiguration as VercelEnvironmentConfiguration

VERCEL = native_definition(
    type="vercel",
    display_name="Vercel Sandbox",
    configuration_model=VercelConnectionConfiguration,
    credential_model=TokenCredential,
    environment_model=VercelEnvironmentConfiguration,
    state_model=NamedTargetState,
    management_type=VercelManagement,
    execution_type=VercelExecution,
    supports_stop=True,
    requires_keepalive=True,
)
