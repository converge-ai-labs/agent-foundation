"""E2B sandboxes in the E2B cloud or another E2B-compatible service.

The SDK owns control API and sandbox traffic. Derive the HTTPS control API from the account domain;
operator network controls own routing restrictions for this opaque transport.
A renewal extends a sandbox by at most its recipe's timeout, which must cover the renewal horizon.
"""

from dataclasses import replace

from a13n_harness.providers.environment.e2b.configuration import (
    E2BConnectionConfiguration,
    E2BCredential,
    E2BEnvironmentConfiguration,
)
from a13n_harness.providers.environment.e2b.provider import E2B as HARNESS_E2B
from a13n_harness.providers.environment.management import EnvironmentProviderConfiguration
from pydantic import BaseModel, Field

from a13n_service.settings import RENEWAL_HORIZON_SECONDS


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


class Recipe(E2BEnvironmentConfiguration):
    timeout_seconds: int = Field(default=3600, ge=RENEWAL_HORIZON_SECONDS, le=86_400, title="Sandbox timeout (seconds)")


async def _runtime(*, configuration: BaseModel, credential: E2BCredential | None) -> object:
    if not isinstance(configuration, Account):
        raise TypeError("E2B requires its Service account configuration")
    assert HARNESS_E2B.runtime_factory is not None
    return await HARNESS_E2B.runtime_factory(configuration=configuration.connection(), credential=credential)


E2B = replace(
    HARNESS_E2B,
    configuration_model=Account,
    environment_model=Recipe,
    runtime_factory=_runtime,
    backend_identity=lambda configuration: HARNESS_E2B.backend_identity(configuration.connection()),
)
