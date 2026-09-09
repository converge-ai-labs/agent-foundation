"""HTTP process entrypoint that begins application drain before transport shutdown."""

from __future__ import annotations

import socket

import uvicorn
from fastapi import FastAPI

from a13n_service.process.runtime import ProcessRuntime
from a13n_service.settings import Settings

# Transport waits must not postpone lifespan cleanup indefinitely. Worker Run
# ownership retains its independently configured drain deadline and lease fencing.
_HTTP_DRAIN_SECONDS = 30


class ServiceServer(uvicorn.Server):
    def __init__(self, app: FastAPI, *, host: str | None = None) -> None:
        settings: Settings = app.state.settings
        super().__init__(
            uvicorn.Config(
                app,
                host=host or settings.host,
                port=settings.port,
                log_config=None,
                workers=1,
                timeout_graceful_shutdown=_HTTP_DRAIN_SECONDS,
            )
        )
        self._application = app

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        runtime: ProcessRuntime | None = getattr(self._application.state, "runtime", None)
        if runtime is not None:
            runtime.begin_drain()
        await super().shutdown(sockets=sockets)


def serve_app(app: FastAPI, *, host: str | None = None) -> None:
    """Run the same bounded shutdown lifecycle for every Service composition."""
    ServiceServer(app, host=host).run()
