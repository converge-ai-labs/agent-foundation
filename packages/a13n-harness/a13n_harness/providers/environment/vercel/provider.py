"""Vercel provider implementation."""

from a13n_harness.providers.environment.native.configuration import NamedTargetState, TokenCredential
from a13n_harness.providers.environment.native.environment import native_definition
from a13n_harness.providers.environment.vercel.execution import VercelExecution
from a13n_harness.providers.environment.vercel.shared import (
    VercelConnectionConfiguration as VercelConnectionConfiguration,
)
from a13n_harness.providers.environment.vercel.shared import (
    VercelEnvironmentConfiguration as VercelEnvironmentConfiguration,
)

VERCEL = native_definition(
    type="vercel",
    display_name="Vercel Sandbox",
    configuration_model=VercelConnectionConfiguration,
    credential_model=TokenCredential,
    environment_model=VercelEnvironmentConfiguration,
    state_model=NamedTargetState,
    execution_type=VercelExecution,
)
