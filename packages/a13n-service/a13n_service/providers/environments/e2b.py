"""E2B as the Service offers it: the E2B cloud, reached with the account's API key.

The E2B SDK dials the control API and every sandbox under one domain (`api.<domain>` and
`<port>-<sandbox>.<domain>`) with its own HTTP client. No check before a dial can cover the sandbox names a
tenant's DNS would answer, so an account names no domain or API URL and reaches only E2B's own.

A renewal extends a sandbox by at most its recipe's timeout, so the timeout is at least the renewal horizon.
"""

from dataclasses import replace

from a13n_environment.e2b.configuration import (
    E2BConnectionConfiguration,
    E2BCredential,
    E2BEnvironmentConfiguration,
)
from a13n_environment.e2b.provider import E2B as BASE_E2B
from a13n_environment.e2b.provider import E2BProviderRuntime
from a13n_environment.management import EnvironmentProviderConfiguration
from a13n_environment.models import EnvironmentState
from pydantic import BaseModel, Field

from a13n_service.settings import RENEWAL_HORIZON_SECONDS

_CLOUD = E2BConnectionConfiguration()


class CloudRecipe(E2BEnvironmentConfiguration):
    timeout_seconds: int = Field(default=3600, ge=RENEWAL_HORIZON_SECONDS, le=86_400, title="Sandbox timeout (seconds)")


async def _provider(*, configuration: BaseModel, credential: E2BCredential | None, runtime: E2BProviderRuntime | None):
    return await BASE_E2B.open_provider(configuration=_CLOUD, credential=credential, runtime=runtime)


def _connector(
    *,
    configuration: BaseModel,
    credential: E2BCredential | None,
    environment: E2BEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: E2BProviderRuntime | None,
):
    return BASE_E2B.execution_connector(
        environment,
        configuration=_CLOUD,
        credential=credential,
        environment_id=environment_id,
        state=state,
        runtime=runtime,
    )


E2B = replace(
    BASE_E2B,
    configuration_model=EnvironmentProviderConfiguration,
    environment_model=CloudRecipe,
    provider_factory=_provider,
    connector_factory=_connector,
    backend_identity=lambda configuration: BASE_E2B.backend_identity(_CLOUD),
)
