"""Explicit single-Worker reverse EIP wiring for the owned live-test Host."""

from contextlib import asynccontextmanager
from dataclasses import replace
from urllib.parse import urlsplit

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.environment.remote_envd.connections import WebSocketEnvdConnections
from a13n_harness.providers.environment.remote_envd.websocket import WEBSOCKET_ENVD, WebSocketEnvdProviderRuntime
from websockets.asyncio.server import serve


class ReverseEnvdHost:
    def __init__(self, configuration, builtin_keys):
        self.configuration = configuration
        self.connections = WebSocketEnvdConnections()
        runtime = WebSocketEnvdProviderRuntime(self.connections)

        async def provide_runtime(**arguments: object) -> WebSocketEnvdProviderRuntime:
            """This Host already owns the connection SDK; no runtime is acquired per target."""
            del arguments
            return runtime

        self.catalog = ProviderCatalog(
            (
                *select_builtin_environment_providers(tuple(key for key in builtin_keys if key != "websocket_envd")),
                replace(WEBSOCKET_ENVD, runtime_factory=provide_runtime),
            )
        )

    def install(self, app):
        original = app.router.lifespan_context
        configuration = self.configuration

        def authorize(connection, request):
            if request.path != "/envd" or request.headers.get("Authorization") != "Bearer " + configuration["token"]:
                return connection.respond(401, "unauthorized")
            return None

        async def attach(connection):
            # Identity comes from the fixture-owned configuration, never from the peer.
            await self.connections.attach(configuration["native_id"], connection)

        @asynccontextmanager
        async def lifespan(application):
            async with self.connections:
                async with serve(
                    attach,
                    "127.0.0.1",
                    urlsplit(configuration["origin"]).port,
                    subprotocols=["eip.v1"],
                    process_request=authorize,
                    compression=None,
                ):
                    async with original(application):
                        yield

        app.router.lifespan_context = lifespan
