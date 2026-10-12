from __future__ import annotations

from collections.abc import Awaitable
from typing import Protocol, runtime_checkable

import anyio
from pydantic import BaseModel


@runtime_checkable
class ClosableRuntime(Protocol):
    """A client or Device with an explicit owner that outlives its borrowers."""

    async def close(self) -> None: ...


class RuntimeFactory[C: BaseModel, K: BaseModel, R](Protocol):
    def __call__(self, *, configuration: C, credential: K | None) -> Awaitable[R]: ...


async def _release(target: ClosableRuntime | None, runtime: ClosableRuntime | None) -> None:
    """Attempt every owned cleanup even when the first one fails."""
    with anyio.CancelScope(shield=True):
        try:
            if target is not None:
                await target.close()
        finally:
            if runtime is not None:
                await runtime.close()
