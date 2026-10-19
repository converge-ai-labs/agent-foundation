"""Connect a provider-selected target to EIP without managing its lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from a13n_envd_client import EIPSession

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import provider_error
from a13n_harness.providers.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
    RemoteEnvdEnvironmentConfiguration,
)
from a13n_harness.providers.environment.remote_envd.environment import REQUIRED_METHODS
from a13n_harness.providers.environment.remote_envd.http import HttpEnvdProviderRuntime


@asynccontextmanager
async def managed_http_session(
    *,
    provider_key: str,
    connection: HttpEnvdConnectionConfiguration,
    endpoint_policy: EndpointPolicy,
    load_credential: Callable[[], Awaitable[HttpEnvdCredential]],
    expected_device_id: str,
    configuration: RemoteEnvdEnvironmentConfiguration,
) -> AsyncIterator[EIPSession]:
    """Own one connection and Session on an already resolved, fixed target.

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
    credential = await load_credential()
    async with HttpEnvdProviderRuntime(connection, credential) as runtime:
        async with runtime.open_session(
            expected_device_id=expected_device_id,
            working_directory=configuration.working_directory,
            required_methods=REQUIRED_METHODS | frozenset(configuration.required_methods),
            egress=configuration.egress,
            expected_boundary=configuration.expected_boundary,
        ) as session:
            yield session
