"""Private Linux VNC viewer transport."""

from __future__ import annotations

import asyncio


class VncConnection:
    """A single revocable viewer transport. Linux enforces input authority natively."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader, self.writer = reader, writer

    async def read(self, size: int) -> bytes:
        return await self.reader.read(size)

    async def send(self, data: bytes) -> None:
        if self.is_closing():
            return
        self.writer.write(data)
        await self.writer.drain()

    def is_closing(self) -> bool:
        return self.writer.is_closing()

    def close(self) -> None:
        self.writer.close()

    async def wait_closed(self) -> None:
        try:
            await self.writer.wait_closed()
        except ConnectionError:
            # A peer reset is already a closed transport, not an input lease.
            pass
