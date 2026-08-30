"""Aggregate-wide Environment run extension contracts."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from a13n_harness.identity import AgentInstanceContext

    from .providers import BoundEnvironment as Environment


@dataclass(frozen=True, slots=True)
class EnvironmentRunExtensionContext:
    """Stable aggregate context supplied to one Environment run extension."""

    run_id: str
    instance: AgentInstanceContext
    environment: Environment


type EnvironmentRunCallback = Callable[[EnvironmentRunExtensionContext], Awaitable[None]]


@runtime_checkable
class EnvironmentRunExtension(Protocol):
    """Trusted resource scope spanning one complete entered Environment aggregate."""

    @property
    def extension_id(self) -> str: ...

    def bind(
        self,
        *,
        context: EnvironmentRunExtensionContext,
    ) -> AbstractAsyncContextManager[None]:
        """Enter this extension once for the aggregate lifecycle."""
        ...


@dataclass(frozen=True, slots=True)
class EnvironmentRunCallbacks:
    """Callback adapter for one paired Environment run-extension scope."""

    extension_id: str
    on_enter: EnvironmentRunCallback | None = None
    on_exit: EnvironmentRunCallback | None = None

    def __post_init__(self) -> None:
        if self.on_enter is None and self.on_exit is None:
            raise ValueError("EnvironmentRunCallbacks requires on_enter or on_exit")
        if self.on_enter is not None and not callable(self.on_enter):
            raise TypeError("on_enter must be an async callable or None")
        if self.on_exit is not None and not callable(self.on_exit):
            raise TypeError("on_exit must be an async callable or None")

    @asynccontextmanager
    async def bind(
        self,
        *,
        context: EnvironmentRunExtensionContext,
    ) -> AsyncGenerator[None]:
        """Run paired callbacks under the authoritative extension lifecycle."""
        if self.on_enter is not None:
            await self.on_enter(context)
        try:
            yield
        finally:
            if self.on_exit is not None:
                await self.on_exit(context)
