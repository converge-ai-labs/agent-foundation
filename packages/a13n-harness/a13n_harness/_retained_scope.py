"""Reference-counted ownership of task-affine async resource scopes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any


class RetainedScope:
    """Enter and exit in one owning task; invocation leases may outlive the caller.

    Closing releases the foreground interest. It waits for teardown only when no
    invocation holds the scope. The final invocation release waits for teardown,
    so execution completion includes release of its retained resources.
    """

    def __init__(self, manager: AbstractAsyncContextManager[Any]) -> None:
        self._manager = manager
        self._leases = 0
        self._closing = False
        self._released = asyncio.Event()
        self._ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._task = asyncio.create_task(self._own())

    async def _own(self) -> None:
        try:
            async with self._manager:
                self._ready.set_result(None)
                await self._released.wait()
        except BaseException as exc:
            if not self._ready.done():
                self._ready.set_exception(exc)
                return
            raise

    async def ready(self) -> None:
        try:
            await asyncio.shield(self._ready)
        except asyncio.CancelledError:
            if not self._ready.done():
                self._ready.cancel()
                self._task.cancel()
            await self.close()
            raise
        except BaseException:
            await self.close()
            raise

    def retain(self) -> Callable[[], Awaitable[None]]:
        if self._closing:
            raise RuntimeError("Resource scope no longer admits invocations")
        self._leases += 1

        async def release() -> None:
            self._leases -= 1
            if self._closing and self._leases == 0:
                self._released.set()
                await asyncio.shield(self._task)

        return release

    async def close(self) -> None:
        self._closing = True
        if self._leases == 0:
            self._released.set()
            await asyncio.shield(self._task)
