"""Supervision contract for process-owned background components."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BackgroundTask:
    """One critical component that must run for the process lifetime."""

    name: str
    run: Callable[[], Awaitable[None]]
    return_is_expected: Callable[[], bool] | None = None
    shutdown: Callable[[], Awaitable[None]] | None = None


async def shutdown_background_components(components: Sequence[BackgroundTask]) -> None:
    """Drain in composition order before the process cancels remaining tasks."""
    for component in components:
        if component.shutdown is not None:
            await component.shutdown()


async def run_critical_component(
    name: str,
    run: Callable[[], Awaitable[None]],
    return_is_expected: Callable[[], bool] | None = None,
) -> None:
    """Fail the process if a critical component returns normally."""

    await run()
    if return_is_expected is not None and return_is_expected():
        return
    raise RuntimeError(f"critical component returned unexpectedly: {name}")


__all__ = ["BackgroundTask", "run_critical_component", "shutdown_background_components"]
