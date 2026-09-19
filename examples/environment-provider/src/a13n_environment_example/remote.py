"""Copyable Host integration: external state, fresh adapters, and non-destructive close."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.models import EnvironmentState
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
    RemoteEnvdEnvironmentConfiguration,
    WebSocketEnvdConnectionConfiguration,
)
from a13n_harness.providers.environment.remote_envd.connections import WebSocketEnvdConnections
from a13n_harness.providers.environment.remote_envd.http import HTTP_ENVD, HttpEnvdProviderRuntime
from a13n_harness.providers.environment.remote_envd.websocket import WEBSOCKET_ENVD, WebSocketEnvdProviderRuntime
from pydantic import SecretStr
from websockets.asyncio.server import ServerConnection, serve
from websockets.http11 import Request, Response
from websockets.typing import Subprotocol


@dataclass(frozen=True)
class RemoteExampleResult:
    provider_key: str
    text: str
    same_generation: bool
    state: EnvironmentState


async def use_remote[R](
    provider: EnvironmentProviderDefinition[Any, Any, RemoteEnvdEnvironmentConfiguration, R],
    runtime: R,
    *,
    daemon_environment_id: str,
) -> RemoteExampleResult:
    """Two independent Runs share files, not their process-local Environment adapter."""
    state = EnvironmentState(
        provider_key=provider.type,
        state_version="1",
        state={"daemon_environment_id": daemon_environment_id},
    )
    configuration = RemoteEnvdEnvironmentConfiguration(required_methods=("file.read_text", "file.write_text"))
    generations: list[str] = []
    text = ""
    for index in range(2):
        environment = provider.construct(
            configuration=configuration,
            environment_id="env-example",  # Host identity, distinct from daemon identity.
            state=state,
            runtime=runtime,
        )
        try:
            await environment.enter(mount_id="workspace")
            await environment.ensure_ready(frozenset({"files"}))
            files = environment.operations.files
            assert files is not None
            if index == 0:
                await files.write_text("/provider-example.txt", "hello from remote envd\n", mode="upsert")
            text = (await files.read_text("/provider-example.txt")).text
            generations.append(environment.descriptor.generation)
        finally:
            current = environment.dump_state()
            assert current is not None
            state = current  # Persist this under your Host's authority, not in the Provider.
            await environment.close()  # Does not stop the remote daemon.
    return RemoteExampleResult(provider.type, text, generations[0] == generations[1], state)


async def run_http(endpoint: str, token: SecretStr, daemon_environment_id: str) -> RemoteExampleResult:
    runtime = HttpEnvdProviderRuntime(
        configuration=HttpEnvdConnectionConfiguration(endpoint=endpoint),
        credential=HttpEnvdCredential(token=token),
    )
    return await use_remote(HTTP_ENVD, runtime, daemon_environment_id=daemon_environment_id)


async def run_websocket(*, token: SecretStr, daemon_environment_id: str, port: int = 8788) -> RemoteExampleResult:
    """This listener is example Host code, NOT a library-owned service.

    A production Host substitutes its own authentication, routing and lifespan.
    One token selects one daemon here only to keep the integration example small.
    """
    async with WebSocketEnvdConnections() as connections:

        def authorize(connection: ServerConnection, request: Request) -> Response | None:
            if request.headers.get("Authorization") != f"Bearer {token.get_secret_value()}":
                return connection.respond(401, "unauthorized")
            return None

        async def handler(connection: ServerConnection) -> None:
            # Identity is selected by trusted Host authentication, not an EIP claim.
            await connections.attach(daemon_environment_id, connection)

        async with serve(
            handler,
            "127.0.0.1",
            port,
            subprotocols=[Subprotocol("eip.v1")],
            process_request=authorize,
            compression=None,
        ):
            print(f"Host example listening on ws://127.0.0.1:{port}; waiting for envd", flush=True)
            runtime = WebSocketEnvdProviderRuntime(
                connections, WebSocketEnvdConnectionConfiguration(connection_timeout=60)
            )
            return await use_remote(WEBSOCKET_ENVD, runtime, daemon_environment_id=daemon_environment_id)
