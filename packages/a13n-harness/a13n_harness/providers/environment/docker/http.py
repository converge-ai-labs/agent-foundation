"""Attach EIP to an existing Docker-managed envd; never start or replace it."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from a13n_envd_client import EIPSession
from pydantic import SecretStr

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import provider_error
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.session_recovery import EnvdSessionRecovery
from a13n_harness.providers.environment.docker.commands import DockerCommands
from a13n_harness.providers.environment.docker.errors import engine_errors
from a13n_harness.providers.environment.docker.shared import DockerReference, _missing
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
)
from a13n_harness.providers.environment.remote_envd.environment import REQUIRED_METHODS, RemoteEnvdExecution
from a13n_harness.providers.environment.remote_envd.http import HttpEnvdProviderRuntime
from a13n_harness.providers.environment.remote_envd.managed import ManagedCredentialCache, managed_http_runtime


class HttpReference(DockerReference):
    """Inert target lookup; the EIP Session context owns every acquired resource."""

    async def close(self) -> None:
        pass


@asynccontextmanager
async def http_runtime(
    reference: DockerReference, *, credential_cache: ManagedCredentialCache | None = None
) -> AsyncGenerator[HttpEnvdProviderRuntime]:
    if reference.target is None:
        raise _missing()
    with engine_errors(mutation=False):
        container = await asyncio.to_thread(reference._lookup)
    if container is None:
        raise _missing()
    if container.status != "running":
        raise provider_error("docker", "environment_stopped", Category.UNAVAILABLE)
    connection = reference.runtime.envd_http
    if connection is None:
        raise provider_error(
            "docker",
            "envd_http_configuration_required",
            Category.INVALID,
            description="Configure the Docker connection's separate envd HTTP route before using EIP execution",
        )
    # Inspect the exact owned container on every attachment: Docker may reassign
    # its published port after a restart. Never infer a host from docker_host.
    bindings = container.attrs.get("NetworkSettings", {}).get("Ports", {}).get("8888/tcp") or []
    if len(bindings) != 1 or bindings[0].get("HostIp") != connection.listen_address:
        raise provider_error("docker", "envd_http_binding_invalid", Category.INVALID)
    try:
        port = int(bindings[0]["HostPort"])
        if not 1 <= port <= 65535:
            raise ValueError("Invalid port")
        configuration = HttpEnvdConnectionConfiguration(
            endpoint=f"http://{connection.host}:{port}",
            allow_plaintext_private_link=connection.allow_plaintext_private_link,
            request_timeout=reference.config.request_timeout_seconds,
        )
    except (KeyError, TypeError, ValueError):
        raise provider_error("docker", "envd_http_binding_invalid", Category.INVALID) from None
    async with managed_http_runtime(
        provider_key="docker",
        connection=configuration,
        endpoint_policy=reference.runtime.endpoint_policy,
        load_credential=lambda: _credential(reference),
        credential_cache=credential_cache,
        target_id=reference.target.container_id,
    ) as runtime:
        yield runtime


@asynccontextmanager
async def http_session(
    reference: DockerReference, *, session_recovery: EnvdSessionRecovery | None = None
) -> AsyncGenerator[EIPSession]:
    async with http_runtime(reference) as runtime:
        async with runtime.open_session(
            expected_device_id=reference.allocation_id,
            session_recovery=session_recovery,
            working_directory="/workspace",
            required_methods=REQUIRED_METHODS
            | (frozenset({"computer.describe", "computer.observe"}) if reference.config.desktop else frozenset()),
        ) as session:
            yield session


async def _credential(reference: DockerReference) -> HttpEnvdCredential:
    assert reference.target is not None
    connection = reference.runtime.envd_http
    assert connection is not None
    commands = DockerCommands(reference.runtime.engine, reference.target.container_id, reference.config)
    try:
        await reference._check_initialized(commands)
        token = await commands.execute(
            [
                reference.config.python,
                "-I",
                "-c",
                f"import sys; sys.stdout.buffer.write(open({connection.credential_file!r},'rb').read(4097))",
            ],
            maximum=4096,
            mutation=False,
        )
    finally:
        commands.closed = True
    try:
        return HttpEnvdCredential(token=SecretStr(token.decode()))
    except ValueError:
        raise provider_error("docker", "envd_credential_invalid", Category.UNAVAILABLE) from None


def http_execution(
    reference: DockerReference, *, session_recovery: EnvdSessionRecovery | None = None
) -> RemoteEnvdExecution:
    if reference.state is None:
        raise EnvironmentError("Docker target state is required", code="environment_not_found")
    return RemoteEnvdExecution(
        environment=reference.config.environment,
        provider_key="docker",
        environment_id=reference.environment_id,
        state=reference.state,
        working_directory="/workspace",
        session_context=http_session(reference, session_recovery=session_recovery),
    )
