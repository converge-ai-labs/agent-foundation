"""One originating incarnation's response reader, shared by all process roles."""

from __future__ import annotations

import asyncio

from anyio import move_on_after
from redis.asyncio import Redis

from .relay_storage import ResponseMailbox
from .relay_waiters import RelayResponseDispatcher


class RelayResponseRuntime:
    def __init__(self, redis: Redis, reader: Redis, instance_id: str) -> None:
        self.instance_id = instance_id
        self._mailbox = ResponseMailbox(redis, instance_id, reader=reader)
        self.responses = RelayResponseDispatcher(self._mailbox)
        self._closed = asyncio.Event()

    async def prepare(self) -> None:
        await self._mailbox.prepare()

    def is_closed(self) -> bool:
        return self._closed.is_set()

    async def _maintain(self) -> None:
        while not self._closed.is_set():
            with move_on_after(30):
                await self._closed.wait()
            if not self._closed.is_set():
                await self._mailbox.touch()

    async def run(self) -> None:
        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(self.responses.run())
                tasks.create_task(self._maintain())
        finally:
            await self.close()

    async def close(self) -> None:
        self.responses.close()
        self._closed.set()
