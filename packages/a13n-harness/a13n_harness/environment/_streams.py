"""Close owned asynchronous readers when a bounded consumer stops early."""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager


@asynccontextmanager
async def closing_stream[T](stream: AsyncIterator[T]) -> AsyncGenerator[AsyncIterator[T]]:
    try:
        yield stream
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()
