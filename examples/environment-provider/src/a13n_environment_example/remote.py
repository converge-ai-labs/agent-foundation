"""Copyable Host integration: external state, fresh adapters, and non-destructive close."""

from __future__ import annotations

from dataclasses import dataclass

from a13n_environment import (
    EnvironmentProvider,
    EnvironmentState,
    HttpEnvdBackendConfiguration,
    HttpEnvdCredential,
    HttpEnvdEnvironmentProvider,
    HttpEnvdProviderRuntime,
    RemoteEnvdProviderConfiguration,
    WebSocketEnvdBackendConfiguration,
    WebSocketEnvdConnections,
    WebSocketEnvdEnvironmentProvider,
    WebSocketEnvdProviderRuntime,
)
from pydantic import SecretStr
from websockets.asyncio.server import ServerConnection, serve
from websockets.http11 import Request, Response
from websockets.typing import Subprotocol


@dataclass(frozen=True)
class RemoteExampleResult:
    provider_key: str
    text: str
    independent_sessions: bool
    state: EnvironmentState


async def use_remote(
    provider: EnvironmentProvider, runtime: HttpEnvdProviderRuntime | WebSocketEnvdProviderRuntime, *, device_id: str
) -> RemoteExampleResult:
    """Two independent Runs share files, not their process-local Environment adapter."""
    state = EnvironmentState(
        provider_key=provider.key,
        state_version="1",
        state={"device_id": device_id},
    )
    if isinstance(runtime, HttpEnvdProviderRuntime):
        device = await runtime.describe(expected_device_id=device_id)
    else:
        device = await runtime.connections.describe(
            expected_device_id=device_id, timeout=runtime.configuration.connection_timeout
        )
    # Discovery opens no Session. Capture an explicit Device cwd for both Runs.
    cwd = device.default_working_directory
    path = f"{cwd.rstrip('/')}/provider-example.txt"
    configuration = RemoteEnvdProviderConfiguration(
        working_directory=cwd, required_methods=("file.read_text", "file.write_text")
    )
    generations: list[str] = []
    text = ""
    for index in range(2):
        environment = provider.create_environment(
            configuration=configuration,
            environment_id="env-example",  # Host identity, distinct from daemon identity.
            state=state,
            runtime=runtime,
        )
        try:
            await environment.enter(
                thread_id="thread-example",
                run_id=f"run-{index + 1}",
                agent_instance_id="agent-example",
                mount_id="workspace",
            )
            await environment.ensure_ready(frozenset({"files"}))
            files = environment.operations.files
            assert files is not None
            if index == 0:
                await files.write_text(path, "hello from remote envd\n", mode="upsert")
            text = (await files.read_text(path)).text
            generations.append(environment.descriptor.generation)
        finally:
            current = environment.dump_state()
            assert current is not None
            state = current  # Persist this under your Host's authority, not in the Provider.
            await environment.close()  # Does not stop the remote daemon.
    return RemoteExampleResult(provider.key, text, generations[0] != generations[1], state)


async def run_http(endpoint: str, token: SecretStr, device_id: str) -> RemoteExampleResult:
    async with HttpEnvdProviderRuntime(
        configuration=HttpEnvdBackendConfiguration(endpoint=endpoint),
        credential=HttpEnvdCredential(token=token),
    ) as runtime:
        return await use_remote(HttpEnvdEnvironmentProvider(), runtime, device_id=device_id)


async def run_websocket(*, token: SecretStr, device_id: str, port: int = 8788) -> RemoteExampleResult:
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
            await connections.attach(device_id, connection)

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
                connections, WebSocketEnvdBackendConfiguration(connection_timeout=60)
            )
            return await use_remote(WebSocketEnvdEnvironmentProvider(), runtime, device_id=device_id)
