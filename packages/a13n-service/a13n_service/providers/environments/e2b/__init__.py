"""E2B sandboxes in the E2B cloud or another E2B-compatible service.

The SDK owns control API and sandbox traffic. Derive the HTTPS control API from the account domain;
operator network controls own routing restrictions for this opaque transport.
A renewal extends a sandbox by at most its recipe's timeout, which must cover the renewal horizon.
"""

from dataclasses import replace

from a13n_harness.environment.models import EnvironmentState
from a13n_harness.environment.session_recovery import EnvdSessionRecovery
from a13n_harness.providers.environment.configuration import EnvironmentProviderConfiguration
from a13n_harness.providers.environment.e2b.configuration import (
    E2BConnectionConfiguration,
    E2BCredential,
    E2BEnvironmentConfiguration,
)
from a13n_harness.providers.environment.e2b.provider import E2BProviderRuntime
from pydantic import BaseModel, Field

from a13n_service.settings import RENEWAL_HORIZON_SECONDS

from .configuration import E2BRecipe
from .definition import E2B as BASE_E2B


class Account(EnvironmentProviderConfiguration):
    domain: str = Field(
        default="e2b.dev",
        title="Sandbox domain",
        description="Sandbox domain of the E2B-compatible service, without a scheme, port or path.",
        max_length=253,
        pattern=r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    )

    def connection(self) -> E2BConnectionConfiguration:
        return E2BConnectionConfiguration(domain=self.domain)


class CloudRecipe(E2BRecipe):
    timeout_seconds: int = Field(default=3600, ge=RENEWAL_HORIZON_SECONDS, le=86_400, title="Sandbox timeout (seconds)")


async def _provider(*, configuration: BaseModel, credential: E2BCredential | None, runtime: E2BProviderRuntime | None):
    if not isinstance(configuration, Account):
        raise TypeError("E2B requires its Service account configuration")
    return await BASE_E2B.open_provider(
        configuration=configuration.connection(), credential=credential, runtime=runtime
    )


def _connector(
    *,
    configuration: BaseModel,
    credential: E2BCredential | None,
    environment: E2BEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: E2BProviderRuntime | None,
    session_recovery: EnvdSessionRecovery | None = None,
):
    if not isinstance(configuration, Account):
        raise TypeError("E2B requires its Service account configuration")
    return BASE_E2B.execution.execution_connector(
        environment,
        configuration=configuration.connection(),
        credential=credential,
        environment_id=environment_id,
        state=state,
        runtime=runtime,
        session_recovery=session_recovery,
    )


E2B = replace(
    BASE_E2B,
    configuration_model=Account,
    environment_model=CloudRecipe,
    provider_factory=_provider,
    execution=replace(
        BASE_E2B.execution,
        configuration_model=Account,
        connector_factory=_connector,
        backend_identity=lambda configuration: BASE_E2B.backend_identity(configuration.connection()),
    ),
)
