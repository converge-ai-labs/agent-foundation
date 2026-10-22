"""Loopback ASGI servers with an explicit startup signal."""

import asyncio
import socket
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Literal

import uvicorn


class _Server(uvicorn.Server):
    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self.ready = asyncio.Event()

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        try:
            await super().startup(sockets)
        except SystemExit as error:
            # Uvicorn uses process exit for lifespan/bind failures; a test server owns only its task.
            raise RuntimeError("Test HTTP server exited before startup completed") from error
        self.ready.set()


@asynccontextmanager
async def serve_http(app: Any, *, lifespan: Literal["auto", "on", "off"] = "auto") -> AsyncGenerator[str]:
    """Serve on an ephemeral port; cancel unfinished responses after 0.1 s during shutdown."""
    server = _Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=0, log_level="warning", lifespan=lifespan, timeout_graceful_shutdown=0.1
        )
    )
    task = asyncio.create_task(server.serve())
    # Wake the caller on startup failure too, so the original exception is reported instead of polling forever.
    task.add_done_callback(lambda _: server.ready.set())
    try:
        await asyncio.wait_for(server.ready.wait(), timeout=10)
        if not server.started:
            await task
            raise RuntimeError("Test HTTP server exited before startup completed")
        yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    finally:
        server.should_exit = True
        if not server.started:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        else:
            await task
