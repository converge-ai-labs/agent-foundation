"""Bounded cleanup without crossing the cancel scopes owned by entered resources."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from anyio import CancelScope, current_time
from anyio.lowlevel import checkpoint_if_cancelled


@asynccontextmanager
async def attempt_resource_stack(*, cleanup_timeout_seconds: float) -> AsyncIterator[AsyncExitStack]:
    # Enter the shield before resource-owned task groups. Turning it on at teardown
    # preserves their nesting; opening a new shield around aclose would not.
    with CancelScope() as scope:
        async with AsyncExitStack() as resources:
            try:
                yield resources
            finally:
                scope.shield = True
                scope.deadline = current_time() + cleanup_timeout_seconds
    if scope.cancel_called:
        raise TimeoutError("Attempt resource cleanup timed out")
    await checkpoint_if_cancelled()
