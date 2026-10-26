"""Resolve an owned Modal target's TLS tunnel and attach shared EIP without starting it."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from pydantic import SecretStr

from a13n_harness.environment.attachments import EIPSessionBinding
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.session_recovery import EnvdSessionRecovery
from a13n_harness.providers.environment.modal.shared import ModalReference, ModalState, execute_command, sdk_errors
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
)
from a13n_harness.providers.environment.remote_envd.environment import REQUIRED_METHODS, RemoteEnvdExecution
from a13n_harness.providers.environment.remote_envd.managed import managed_http_runtime


def tunnel_connection(url: str, timeout: float = 120) -> HttpEnvdConnectionConfiguration:
    """Accept only a TLS tunnel origin before loading any guest credentials."""
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Invalid tunnel origin")
        return HttpEnvdConnectionConfiguration(endpoint=url, request_timeout=timeout)
    except ValueError:
        raise failure("modal", "provider_response_invalid", Category.UNAVAILABLE) from None


class HttpReference(ModalReference):
    reuse_transport = True

    def observe(self, state: ModalState) -> None:
        if state != self.target:
            raise failure(self.provider_key, "provider_target_changed", Category.CONFLICT)

    async def close(self) -> None:
        await self.close_transport()


@asynccontextmanager
async def http_session(
    reference: ModalReference, *, session_recovery: EnvdSessionRecovery | None = None
) -> AsyncGenerator[EIPSessionBinding]:
    try:
        reference.require_target()
        sandbox = await reference.lookup()
        if sandbox is None:
            raise failure("modal", "provider_target_unavailable", Category.UNAVAILABLE)
        reference.sandbox = sandbox
        with sdk_errors():
            tunnels = await sandbox.tunnels.aio(timeout=int(reference.config.request_timeout_seconds))
        tunnel = tunnels.get(8888)
        if tunnel is None:
            raise failure("modal", "envd_tunnel_missing", Category.INVALID)
        connection = tunnel_connection(tunnel.url, reference.config.request_timeout_seconds)

        async def credential() -> HttpEnvdCredential:
            token = await execute_command(
                sandbox,
                [
                    "python3",
                    "-I",
                    "-c",
                    "import sys; sys.stdout.write(open('/var/lib/a13n-envd/http-token').read(4097))",
                ],
                10,
            )
            try:
                return HttpEnvdCredential(token=SecretStr(token))
            except ValueError:
                raise failure("modal", "envd_credential_invalid", Category.UNAVAILABLE) from None

        assert reference.target is not None
        async with managed_http_runtime(
            provider_key="modal",
            target_id=reference.target.target_id,
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
                # EIP uses its own HTTP transport. Return the SDK lease before Agent
                # execution so desktop requests can borrow the same Sandbox safely.
                if reference._lease is not None:
                    await reference.close_transport()
                yield session
    except BaseException:
        reference.discard_transport()
        raise
    finally:
        await reference.close_transport()


def http_execution(
    reference: ModalReference, *, session_recovery: EnvdSessionRecovery | None = None
) -> RemoteEnvdExecution:
    reference.require_target()
    assert reference.state is not None
    return RemoteEnvdExecution(
        environment=reference.config.environment,
        provider_key="modal",
        environment_id=reference.environment_id,
        state=reference.state,
        working_directory="/workspace",
        session_context=http_session(reference, session_recovery=session_recovery),
    )
