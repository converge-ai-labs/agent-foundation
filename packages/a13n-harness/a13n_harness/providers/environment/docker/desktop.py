"""Attach EIP to an existing managed Docker desktop; never start or replace it."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from a13n_envd_client import EIPSession
from pydantic import SecretStr

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import provider_error
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.providers.environment.docker.commands import DockerCommands
from a13n_harness.providers.environment.docker.errors import engine_errors
from a13n_harness.providers.environment.docker.shared import DockerReference, _missing
from a13n_harness.providers.environment.docker.tunnel import DesktopTunnel
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
)
from a13n_harness.providers.environment.remote_envd.environment import REQUIRED_METHODS, RemoteEnvdExecution
from a13n_harness.providers.environment.remote_envd.http import HttpEnvdProviderRuntime

DESKTOP_TOKEN_PATH = "/var/lib/a13n-envd/desktop-token"


class DesktopReference(DockerReference):
    """Inert target lookup; the desktop Session context owns every acquired resource."""

    async def close(self) -> None:
        pass


@asynccontextmanager
async def desktop_session(reference: DockerReference) -> AsyncIterator[EIPSession]:
    if reference.target is None:
        raise _missing()
    with engine_errors(mutation=False):
        container = await asyncio.to_thread(reference._lookup)
    if container is None:
        raise _missing()
    if container.status != "running":
        raise provider_error("docker", "environment_stopped", Category.UNAVAILABLE)
    commands = DockerCommands(reference.runtime.engine, reference.target.container_id, reference.config)
    try:
        await reference._check_initialized(commands)
        token = await commands.execute(
            [
                reference.config.python,
                "-I",
                "-c",
                f"import sys; sys.stdout.buffer.write(open({DESKTOP_TOKEN_PATH!r},'rb').read(4097))",
            ],
            maximum=4096,
            mutation=False,
        )
    finally:
        commands.closed = True
    try:
        credential = HttpEnvdCredential(token=SecretStr(token.decode()))
    except ValueError:
        raise provider_error("docker", "desktop_credential_invalid", Category.UNAVAILABLE) from None
    async with (
        DesktopTunnel(reference.runtime.engine, reference.target.container_id) as tunnel,
        HttpEnvdProviderRuntime(
            HttpEnvdConnectionConfiguration(
                endpoint=tunnel.endpoint,
                request_timeout=reference.config.request_timeout_seconds,
            ),
            credential,
        ) as runtime,
    ):
        async with runtime.open_session(
            expected_device_id=reference.allocation_id,
            working_directory="/workspace",
            required_methods=REQUIRED_METHODS | {"computer.describe", "computer.observe"},
        ) as session:
            yield session


def desktop_execution(reference: DockerReference) -> RemoteEnvdExecution:
    if reference.state is None:
        raise EnvironmentError("Docker target state is required", code="environment_not_found")
    return RemoteEnvdExecution(
        provider_key="docker",
        environment_id=reference.environment_id,
        state=reference.state,
        working_directory="/workspace",
        session_context=desktop_session(reference),
    )
