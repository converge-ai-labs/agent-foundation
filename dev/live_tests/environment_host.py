"""Explicit single-Worker reverse EIP wiring for the owned live-test Host."""

from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from a13n_environment import (
    WebSocketEnvdConnections,
    WebSocketEnvdEnvironmentProvider,
    build_environment_provider_catalog,
)
from websockets.asyncio.server import serve


class ReverseEnvdHost:
    def __init__(self, configuration, builtin_keys):
        self.configuration = configuration
        self.connections = WebSocketEnvdConnections()
        self.catalog = build_environment_provider_catalog(
            builtin_keys=tuple(key for key in builtin_keys if key != "a13n.websocket-envd"),
            explicit_providers=(WebSocketEnvdEnvironmentProvider(connections=self.connections),),
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
