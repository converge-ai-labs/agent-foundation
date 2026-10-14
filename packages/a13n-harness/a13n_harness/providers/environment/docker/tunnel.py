"""Bounded HTTP byte forwarding over the existing Docker Engine connection.

The loopback listener belongs to one execution scope. Its fixed container-side
peer is envd, which still authenticates every request. No container port is published.
"""

from __future__ import annotations

import asyncio
import contextlib
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from a13n_harness.providers.environment.docker.observation import attach, disconnect, frames
from a13n_harness.providers.environment.docker.runtime import DockerSDKEngine

_CHUNK_BYTES = 65536
_MAX_CONNECTIONS = 16


class DesktopTunnel:
    def __init__(self, engine: DockerSDKEngine, container_id: str) -> None:
        self.engine = engine
        self.container_id = container_id
        self.endpoint = ""
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.Task[None]] = set()
        self._closed = False
        self._io: ThreadPoolExecutor | None = None

    async def __aenter__(self) -> DesktopTunnel:
        self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0, limit=_CHUNK_BYTES)
        # Long-lived Engine reads must not starve Service's shared blocking-I/O pool.
        self._io = ThreadPoolExecutor(max_workers=2 * _MAX_CONNECTIONS, thread_name_prefix="desktop-io")
        self.endpoint = f"http://127.0.0.1:{self._server.sockets[0].getsockname()[1]}"
        return self

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._closed or len(self._clients) >= _MAX_CONNECTIONS:
            writer.close()
            return
        task = asyncio.create_task(self._forward(reader, writer), name="docker-desktop-tunnel")
        self._clients.add(task)

        def finished(task: asyncio.Task[None]) -> None:
            # Cancellation can precede the coroutine's first instruction, so its
            # finally block alone cannot own the accepted socket's cleanup.
            writer.close()
            self._clients.discard(task)

        task.add_done_callback(finished)

    async def _forward(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connection: Any = None
        pumps: list[asyncio.Task[None]] = []
        try:
            created = await asyncio.to_thread(
                self.engine.client.api.exec_create,
                self.container_id,
                ["socat", "STDIO", "TCP4:127.0.0.1:8888,connect-timeout=5"],
                stdin=True,
                stdout=True,
                stderr=True,
                tty=False,
                user="root",
            )
            connection = await attach(self.engine.client.api, created["Id"])
            raw = getattr(connection, "_sock", connection)

            async def upload() -> None:
                while chunk := await reader.read(_CHUNK_BYTES):
                    sending = asyncio.get_running_loop().run_in_executor(self._io, raw.sendall, chunk)
                    try:
                        await asyncio.shield(sending)
                    finally:
                        if not sending.done():
                            await asyncio.to_thread(disconnect, connection)
                        await asyncio.gather(sending, return_exceptions=True)

            async def download() -> None:
                iterator = frames(connection)
                while True:
                    receiving = asyncio.get_running_loop().run_in_executor(self._io, next, iterator, None)
                    try:
                        frame = await asyncio.shield(receiving)
                    finally:
                        if not receiving.done():
                            await asyncio.to_thread(disconnect, connection)
                        await asyncio.gather(receiving, return_exceptions=True)
                    if frame is None:
                        return
                    stream, chunk = frame
                    if stream == 1:
                        writer.write(chunk)
                        await writer.drain()
                    # The relay's stderr is diagnostic, never HTTP bytes or retained output.

            pumps = [asyncio.create_task(upload()), asyncio.create_task(download())]
            await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            raise
        except Exception:
            # The HTTP peer observes EOF, then the EIP layer owns the typed connection failure.
            # Never log request bodies or credentials here, or retry uncertain operations.
            pass
        finally:

            async def cleanup() -> None:
                if connection is not None:
                    await asyncio.to_thread(disconnect, connection)
                for pump in pumps:
                    pump.cancel()
                await asyncio.gather(*pumps, return_exceptions=True)
                writer.close()
                with contextlib.suppress(OSError):
                    await writer.wait_closed()
                if connection is not None:
                    response = getattr(connection, "_response", None)
                    await asyncio.to_thread(response.close if response is not None else connection.close)

            # HTTP EOF may already have started cleanup when scope exit cancels
            # this task. Finish releasing the SDK response even in that ordering.
            closing = asyncio.create_task(cleanup())
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError:
                await closing
                raise

    async def __aexit__(self, *args: object) -> None:
        self._closed = True
        assert self._server is not None
        self._server.close()
        clients = list(self._clients)
        for task in clients:
            task.cancel()
        await asyncio.gather(*clients, return_exceptions=True)
        await self._server.wait_closed()
        if self._io is not None:
            await asyncio.to_thread(self._io.shutdown, wait=True, cancel_futures=True)
