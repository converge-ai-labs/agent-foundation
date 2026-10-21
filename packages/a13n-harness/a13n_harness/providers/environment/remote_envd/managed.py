"""Connect a provider-selected target to EIP without managing its lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import provider_error
from a13n_harness.providers.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
)
from a13n_harness.providers.environment.remote_envd.http import HttpEnvdProviderRuntime


@dataclass(slots=True)
class ManagedCredentialCache:
    """One Host-owned monitoring attachment; discard when its selection changes.

    The caller serializes observations and clears the cache after failed discovery.
    Providers still resolve ownership and routing before looking up this credential.
    """

    key: tuple[str, str, str] | None = None
    credential: HttpEnvdCredential | None = field(default=None, repr=False)

    def clear(self) -> None:
        self.key = None
        self.credential = None


@asynccontextmanager
async def managed_http_runtime(
    *,
    provider_key: str,
    connection: HttpEnvdConnectionConfiguration,
    endpoint_policy: EndpointPolicy,
    load_credential: Callable[[], Awaitable[HttpEnvdCredential]],
    credential_cache: ManagedCredentialCache | None = None,
    target_id: str,
) -> AsyncIterator[HttpEnvdProviderRuntime]:
    """Own a Device carrier on an already resolved, fixed target.

    Providers resolve ownership and routing on each attachment. Credentials are
    loaded only after the actual endpoint passes Host policy; neither connection
    failure nor an uncertain operation triggers allocation, fallback or replay.
    """
    try:
        await endpoint_policy.validate(connection.endpoint)
    except EndpointPolicyError:
        raise provider_error(
            provider_key,
            "envd_endpoint_denied",
            Category.INVALID,
            description="Deployment or Run policy denies the discovered envd HTTP endpoint",
        ) from None
    key = (provider_key, target_id, connection.endpoint)
    credential = None
    if credential_cache is not None and credential_cache.key == key:
        credential = credential_cache.credential
    try:
        if credential is None:
            credential = await load_credential()
        async with HttpEnvdProviderRuntime(connection, credential) as runtime:
            yield runtime
    except BaseException:
        if credential_cache is not None:
            credential_cache.clear()
        raise
    else:
        if credential_cache is not None:
            credential_cache.key = key
            credential_cache.credential = credential
