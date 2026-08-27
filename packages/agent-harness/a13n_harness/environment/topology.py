"""Dynamic Environment topology controller and non-draining observer journal."""

from __future__ import annotations

import asyncio
from typing import Protocol

from .models import EnvironmentError, EnvironmentTopologyChange, EnvironmentTopologyRequest
from .providers import EnvironmentTopologyController, EnvironmentTopologyObserver


class _TopologyApplier(Protocol):
    async def apply_topology(self, request: EnvironmentTopologyRequest) -> EnvironmentTopologyChange: ...


class DynamicTopologyObserver(EnvironmentTopologyObserver):
    """Bounded append-only journal with independent validated cursors."""

    def __init__(self, initial_version: int) -> None:
        self._initial_version = initial_version
        self._current_version = initial_version
        self._versions = {initial_version}
        self._changes: list[EnvironmentTopologyChange] = []
        self._changed = asyncio.Event()
        self._closed = False

    @property
    def initial_topology_version(self) -> int:
        return self._initial_version

    def publish(self, change: EnvironmentTopologyChange) -> None:
        """Append one already committed change without introducing an await point."""
        if self._closed:
            raise EnvironmentError("The Environment observer is closed.", code="environment_closed")
        if change.previous_version != self._current_version or change.current_version <= self._current_version:
            raise EnvironmentError(
                "Environment topology journal change is not contiguous.",
                code="environment_topology_invalid",
            )
        self._changes.append(change)
        self._current_version = change.current_version
        self._versions.add(change.current_version)
        changed = self._changed
        self._changed = asyncio.Event()
        changed.set()

    async def read(self, *, after_version: int, wait: bool = False) -> tuple[EnvironmentTopologyChange, ...]:
        while True:
            self._validate_cursor(after_version)
            matching = tuple(change for change in self._changes if change.current_version > after_version)
            if matching:
                return matching
            if self._closed:
                raise EnvironmentError("The Environment observer is closed.", code="environment_closed")
            if not wait:
                return ()
            changed = self._changed
            await changed.wait()

    def _validate_cursor(self, version: int) -> None:
        if not isinstance(version, int) or isinstance(version, bool) or version not in self._versions:
            raise EnvironmentError(
                "Environment topology observer cursor is not in the committed version chain.",
                code="environment_topology_cursor_invalid",
                details={
                    "after_version": version,
                    "initial_version": self._initial_version,
                    "current_version": self._current_version,
                },
            )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._changed.set()


class DynamicTopologyController(EnvironmentTopologyController):
    """Host-only activation and terminal fence paired to one entered aggregate."""

    def __init__(self) -> None:
        self._activation_changed = asyncio.Event()
        self._accepting = False
        self._closing = False
        self._closed = False
        self._bound: _TopologyApplier | None = None

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def can_apply(self) -> bool:
        return self._accepting and not self._closed

    def attach(self, bound: _TopologyApplier) -> None:
        if self._bound is not None or self._closed:
            raise EnvironmentError(
                "Environment topology controller is already paired or closed.",
                code="environment_binding_reused",
            )
        self._bound = bound

    def activate(self) -> None:
        if not self._closed and not self._closing:
            self._accepting = True
            self._activation_changed.set()

    def begin_close(self) -> None:
        """Install the logical-run terminal fence without introducing an await point."""
        self._accepting = False
        self._closing = True
        self._activation_changed.set()

    async def close(self) -> None:
        self.begin_close()
        self._closed = True

    async def wait_until_active(self) -> None:
        await self._activation_changed.wait()
        if self._closed or self._closing:
            raise EnvironmentError(
                "The Environment closed before controller activation.",
                code="environment_closed",
            )
        if not self._accepting:
            raise EnvironmentError("The Environment controller is not active.", code="run_not_active")

    async def apply(self, request: EnvironmentTopologyRequest) -> EnvironmentTopologyChange:
        if self._closed:
            raise EnvironmentError("The Environment controller is closed.", code="environment_closed")
        if not self._accepting or self._bound is None:
            raise EnvironmentError("The Environment controller is not active.", code="run_not_active")
        return await self._bound.apply_topology(request)
