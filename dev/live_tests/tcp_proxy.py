"""Cut only connections made through a fixture-owned loopback listener."""

import asyncio
from contextlib import asynccontextmanager


class TCPProxy:
    def __init__(self, host: str, port: int):
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Fault proxies require a loopback upstream")
        self.host, self.port = host, port
        self.blocked = False
        self.rejected = 0
        self.connections = 0
        self.writers = set()
        self.tasks = set()

    @asynccontextmanager
    async def listen(self):
        server = await asyncio.start_server(self._connected, "127.0.0.1", 0)
        self.local_port = server.sockets[0].getsockname()[1]
        try:
            yield self
        finally:
            server.close()
            self.cut()
            if self.tasks:
                for task in tuple(self.tasks):
                    task.cancel()
                await asyncio.gather(*self.tasks, return_exceptions=True)
            await server.wait_closed()

    def cut(self):
        self.blocked = True
        for writer in tuple(self.writers):
            writer.close()

    def restore(self):
        self.blocked = False

    def _connected(self, reader, writer):
        task = asyncio.create_task(self._relay(reader, writer))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _relay(self, reader, writer):
        upstream = None
        pumps = []
        self.writers.add(writer)
        try:
            if self.blocked:
                self.rejected += 1
                return
            upstream_reader, upstream = await asyncio.wait_for(self._open_upstream(), 3)
            self.writers.add(upstream)
            if self.blocked:
                self.rejected += 1
                return
            self.connections += 1
            pumps = [
                asyncio.create_task(self._copy(reader, upstream)),
                asyncio.create_task(self._copy(upstream_reader, writer)),
            ]
            await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            if self.blocked:
                self.rejected += 1
        except (ConnectionError, OSError, TimeoutError):
            pass
        finally:
            for task in pumps:
                task.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            for stream in (writer, upstream):
                if stream is not None:
                    self.writers.discard(stream)
                    stream.close()
                    try:
                        await asyncio.wait_for(stream.wait_closed(), 2)
                    except (ConnectionError, OSError, TimeoutError):
                        pass

    async def _open_upstream(self):
        return await asyncio.open_connection(self.host, self.port)

    @staticmethod
    async def _copy(reader, writer):
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
