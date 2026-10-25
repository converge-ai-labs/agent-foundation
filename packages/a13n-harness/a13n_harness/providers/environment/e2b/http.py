"""Attach shared EIP to a validated E2B target, without starting guest services."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from pydantic import SecretStr

from a13n_harness.environment.attachments import EIPSessionBinding
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.session_recovery import EnvdSessionRecovery
from a13n_harness.providers.environment.e2b.connection import close_sandbox, open_sandbox
from a13n_harness.providers.environment.e2b.errors import provider_error, sdk_errors
from a13n_harness.providers.environment.e2b.shared import E2BReference
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
)
from a13n_harness.providers.environment.remote_envd.environment import REQUIRED_METHODS, RemoteEnvdExecution
from a13n_harness.providers.environment.remote_envd.http import HttpEnvdProviderRuntime
from a13n_harness.providers.environment.remote_envd.managed import ManagedCredentialCache, managed_http_runtime


class HttpReference(E2BReference):
    """Inert target lookup; the Session context owns acquired resources."""

    async def close(self) -> None:
        pass


@asynccontextmanager
async def http_runtime(
    reference: E2BReference, *, credential_cache: ManagedCredentialCache | None = None
) -> AsyncGenerator[HttpEnvdProviderRuntime]:
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

        async with managed_http_runtime(
            provider_key="e2b",
            connection=connection,
            endpoint_policy=reference._runtime.endpoint_policy,
            load_credential=credential,
            credential_cache=credential_cache,
            target_id=reference._state.sandbox_id,
        ) as runtime:
            yield runtime
    finally:
        await close_sandbox(sandbox)


@asynccontextmanager
async def http_session(
    reference: E2BReference, *, session_recovery: EnvdSessionRecovery | None = None
) -> AsyncGenerator[EIPSessionBinding]:
    async with http_runtime(reference) as runtime:
        async with runtime.open_session(
            expected_device_id=reference._native_id,
            session_recovery=session_recovery,
            working_directory="/workspace",
            required_methods=REQUIRED_METHODS
            | (
                frozenset({"computer.describe", "computer.observe"})
                if reference._configuration.desktop
                else frozenset()
            ),
        ) as session:
            yield session


def http_execution(
    reference: E2BReference, *, session_recovery: EnvdSessionRecovery | None = None
) -> RemoteEnvdExecution:
    if reference.state is None:
        raise provider_error("provider_state_required", Category.INVALID)
    return RemoteEnvdExecution(
        environment=reference._configuration.environment,
        provider_key="e2b",
        environment_id=reference.environment_id,
        state=reference.state,
        working_directory="/workspace",
        session_context=http_session(reference, session_recovery=session_recovery),
    )
