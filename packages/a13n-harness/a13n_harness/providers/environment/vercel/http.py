"""Authenticated EIP attachment to a validated, already-running Vercel session."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from a13n_envd_client import EIPSession
from pydantic import SecretStr

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.session_recovery import EnvdSessionRecovery
from a13n_harness.providers.environment.native.configuration import NamedTargetState
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
)
from a13n_harness.providers.environment.remote_envd.environment import REQUIRED_METHODS, RemoteEnvdExecution
from a13n_harness.providers.environment.remote_envd.managed import managed_http_runtime
from a13n_harness.providers.environment.vercel.commands import command
from a13n_harness.providers.environment.vercel.shared import VercelReference


class HttpReference(VercelReference):
    async def close(self) -> None:
        await self.close_transport()

    def observe(self, state: NamedTargetState) -> None:
        if state != self.target:
            raise failure(self.provider_key, "provider_target_changed", Category.CONFLICT)


@asynccontextmanager
async def http_session(
    reference: VercelReference, *, session_recovery: EnvdSessionRecovery | None = None
) -> AsyncIterator[EIPSession]:
    try:
        session_id = await reference.running_session()
        connection = HttpEnvdConnectionConfiguration(
            endpoint=reference.endpoint(8888), request_timeout=reference.config.request_timeout_seconds
        )

        async def credential() -> HttpEnvdCredential:
            raw = await command(
                reference,
                session_id,
                [
                    "python3",
                    "-I",
                    "-c",
                    "import sys; sys.stdout.write(open('/var/lib/a13n-envd/http-token').read(4097))",
                ],
                sudo=True,
                timeout=10,
                max_output=4096,
            )
            try:
                return HttpEnvdCredential(token=SecretStr(raw))
            except ValueError:
                raise failure("vercel", "envd_credential_invalid", Category.UNAVAILABLE) from None

        async with managed_http_runtime(
            provider_key="vercel",
            target_id=session_id,
            connection=connection,
            endpoint_policy=reference.backend._endpoint_policy,
            load_credential=credential,
        ) as runtime:
            async with runtime.open_session(
                expected_device_id=reference.owner,
                session_recovery=session_recovery,
                working_directory="/workspace",
                required_methods=REQUIRED_METHODS
                | (frozenset({"computer.describe", "computer.observe"}) if reference.config.desktop else frozenset()),
            ) as session:
                yield session
    finally:
        await reference.close_transport()


def http_execution(
    reference: VercelReference, *, session_recovery: EnvdSessionRecovery | None = None
) -> RemoteEnvdExecution:
    reference.require_target()
    assert reference.state is not None
    return RemoteEnvdExecution(
        environment=reference.config.environment,
        provider_key="vercel",
        environment_id=reference.environment_id,
        state=reference.state,
        working_directory="/workspace",
        session_context=http_session(reference, session_recovery=session_recovery),
    )
