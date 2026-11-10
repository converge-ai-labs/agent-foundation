"""Bounded ordered observation with cancellation-safe worker cleanup."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from anyio import CancelScope


async def map_ordered[T, R](
    items: Sequence[T], read: Callable[[T], Awaitable[R]], *, concurrency: int = 8
) -> AsyncIterator[R | Exception]:
    """Retain one worker window; apply ordinary failures in input order."""

    async def observe(item: T) -> R | Exception:
        try:
            return await read(item)
        except Exception as exc:
            return exc

    for offset in range(0, len(items), concurrency):
        workers = [
            asyncio.create_task(observe(item), name="file-observation") for item in items[offset : offset + concurrency]
        ]
        joined = asyncio.gather(*workers)
        try:
            results = await asyncio.shield(joined)
        finally:
            for task in workers:
                if not task.done():
                    task.cancel()
            # Join workers before releasing a caller's pinned file scope, even
            # under level-triggered Host cancellation.
            with CancelScope(shield=True):
                await asyncio.gather(joined, *workers, return_exceptions=True)
        for result in results:
            yield result
