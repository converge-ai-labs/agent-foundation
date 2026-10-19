"""Attach shared EIP to a validated E2B target, without starting guest services."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from a13n_envd_client import EIPSession
from pydantic import SecretStr

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.providers.environment.e2b.connection import close_sandbox, open_sandbox
from a13n_harness.providers.environment.e2b.errors import provider_error, sdk_errors
from a13n_harness.providers.environment.e2b.shared import E2BReference
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
    RemoteEnvdEnvironmentConfiguration,
)
from a13n_harness.providers.environment.remote_envd.environment import RemoteEnvdExecution
from a13n_harness.providers.environment.remote_envd.managed import managed_http_session


class HttpReference(E2BReference):
    """Inert target lookup; the Session context owns acquired resources."""

    async def close(self) -> None:
        pass


@asynccontextmanager
async def http_session(reference: E2BReference) -> AsyncIterator[EIPSession]:
    if reference._state is None:
        raise provider_error("provider_state_required", Category.INVALID)
    sandbox = await open_sandbox(reference._state.sandbox_id, reference._options(), reference._validate_target)
    try:
        # Resolve from the validated sandbox domain on every attachment; never
        # persist a public bearer URL or reuse the E2B control API credential.
        connection = HttpEnvdConnectionConfiguration(
            endpoint="https://" + sandbox.get_host(8888),
            request_timeout=reference._configuration.request_timeout_seconds,
        )

        async def credential() -> HttpEnvdCredential:
            with sdk_errors():
                result = await sandbox.commands.run(
                    "python3 -I -c \"import sys; sys.stdout.write(open('/var/lib/a13n-envd/http-token').read(4097))\"",
                    user="root",
                    timeout=10,
                    request_timeout=15,
                )
            try:
                return HttpEnvdCredential(token=SecretStr(result.stdout))
            except ValueError:
                raise provider_error("envd_credential_invalid", Category.UNAVAILABLE) from None

        async with managed_http_session(
            provider_key="e2b",
            connection=connection,
            endpoint_policy=reference._runtime.endpoint_policy,
            load_credential=credential,
            expected_device_id=reference._native_id,
            configuration=RemoteEnvdEnvironmentConfiguration(
                working_directory="/workspace",
                required_methods=("computer.describe", "computer.observe") if reference._configuration.desktop else (),
            ),
        ) as session:
            yield session
    finally:
        await close_sandbox(sandbox)


def http_execution(reference: E2BReference) -> RemoteEnvdExecution:
    if reference.state is None:
        raise provider_error("provider_state_required", Category.INVALID)
    return RemoteEnvdExecution(
        provider_key="e2b",
        environment_id=reference.environment_id,
        state=reference.state,
        working_directory="/workspace",
        session_context=http_session(reference),
    )
