"""Aggregate-wide Environment run extension contracts."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
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
