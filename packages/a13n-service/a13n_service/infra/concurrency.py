"""Bounded independent I/O with ordered results and no work left running on return."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import cast


async def map_concurrently[T, R](
    items: Sequence[T], operation: Callable[[T], Awaitable[R]], *, limit: int = 8
) -> list[R]:
    if limit < 1:
        raise ValueError("Concurrency limit must be positive")
    semaphore = asyncio.Semaphore(limit)
    failure: BaseException | None = None

    async def run(item: T) -> R:
        nonlocal failure
        async with semaphore:
            if failure is not None:
                raise failure
            try:
                return await operation(item)
            except BaseException as error:
                failure = error
                raise

    # Settle independent operations before propagating an error; callers can then safely close their resources.
    results = await asyncio.gather(*(run(item) for item in items), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return cast(list[R], results)
