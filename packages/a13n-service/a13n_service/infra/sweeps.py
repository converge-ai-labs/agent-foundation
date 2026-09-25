"""Bounded periodic background work over durable evidence.

Each sweep is one named function that does a bounded amount of work per call. The scheduler only adds
interval, jitter, a deadline, the sweep's log context, and failure logging and counting; coordination between
replicas belongs to the sweep itself (row claims or `SKIP LOCKED`), and no sweep holds a database session across
external I/O.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from a13n_logging import get_logger, log_context

from a13n_service.infra.telemetry import meter

logger = get_logger(__name__)

PASSES = meter.create_counter(
    "a13n.sweep.passes", unit="{pass}", description="Sweep passes by result; a timed-out pass has failed"
)


@dataclass(frozen=True)
class Sweep:
    name: str
    every: float
    run: Callable[[], Awaitable[object]]
    timeout: float


async def _loop(sweep: Sweep) -> None:
    # Jitter spreads replicas started together; later passes keep the configured cadence.
    await asyncio.sleep(random.uniform(0, sweep.every))
    with log_context(sweep=sweep.name):
        while True:
            try:
                async with asyncio.timeout(sweep.timeout):
                    await sweep.run()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("Sweep failed", extra={"error_type": type(error).__name__})
                PASSES.add(1, {"sweep": sweep.name, "result": "failed"})
            else:
                PASSES.add(1, {"sweep": sweep.name, "result": "succeeded"})
            await asyncio.sleep(sweep.every)


def require_unique(sweeps: Sequence[Sweep]) -> None:
    """Called before scheduling, so a duplicate fails startup instead of a background task."""
    names = [sweep.name for sweep in sweeps]
    if duplicates := sorted({name for name in names if names.count(name) > 1}):
        raise ValueError(f"Duplicate sweeps: {duplicates}")


async def run_sweeps(sweeps: Sequence[Sweep]) -> None:
    async with asyncio.TaskGroup() as group:
        for sweep in sweeps:
            group.create_task(_loop(sweep), name=f"sweep-{sweep.name}")
