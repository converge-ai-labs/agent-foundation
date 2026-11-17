"""Bounded periodic background work over durable evidence.

Each sweep is one named function that does a bounded amount of work per call. The scheduler only adds
interval, jitter, a deadline, the sweep's log context, and failure logging and counting; coordination between
replicas belongs to the sweep itself (row claims or `SKIP LOCKED`), and no sweep holds a database session across
external I/O.
"""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from a13n_logging import exception_details, get_logger, log_context

from a13n_service.infra.telemetry import Gauge, meter

logger = get_logger(__name__)

PASSES = meter.create_counter(
    "a13n.sweep.passes", unit="{pass}", description="Sweep passes by result; a timed-out pass has failed"
)

ITEMS = meter.create_counter(
    "a13n.sweep.items", unit="{item}", description="Isolated item calls by outcome; a successful call may be a no-op"
)
DURATION = meter.create_histogram(
    "a13n.sweep.duration",
    unit="s",
    description="Sweep pass duration",
    explicit_bucket_boundaries_advisory=(0.01, 0.1, 1, 5, 15, 30, 60, 300),
)
LAST_SUCCESS = Gauge(
    "a13n.sweep.last_success", unit="s", description="Unix time of last normally completed scan, not item success"
)
DEADLINE = Gauge(
    "a13n.sweep.stale_after",
    unit="s",
    description="Maximum normal gap between completed scans, including pass deadline and jitter",
)


@contextmanager
def item(sweep: str) -> Iterator[None]:
    """Observe an isolated item call without changing its exception or retry policy."""
    try:
        yield
    except Exception:
        ITEMS.add(1, {"sweep": sweep, "result": "failed"})
        raise
    else:
        ITEMS.add(1, {"sweep": sweep, "result": "succeeded"})


@dataclass(frozen=True)
class Sweep:
    name: str
    every: float
    run: Callable[[], Awaitable[object]]
    timeout: float


async def _loop(sweep: Sweep) -> None:
    # Initialize freshness at startup so a scan that never completes is observable too.
    LAST_SUCCESS.set(time.time(), {"sweep": sweep.name})
    DEADLINE.set(3 * (sweep.every + sweep.timeout), {"sweep": sweep.name})
    # Jitter spreads replicas started together; later passes keep the configured cadence.
    await asyncio.sleep(random.uniform(0, sweep.every))
    with log_context(sweep=sweep.name):
        while True:
            started = time.monotonic()
            try:
                async with asyncio.timeout(sweep.timeout):
                    await sweep.run()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "Sweep failed",
                    extra={"error_type": type(error).__name__, "exception_details": exception_details(error)},
                )
                PASSES.add(1, {"sweep": sweep.name, "result": "failed"})
            else:
                PASSES.add(1, {"sweep": sweep.name, "result": "succeeded"})
                LAST_SUCCESS.set(time.time(), {"sweep": sweep.name})
            finally:
                DURATION.record(time.monotonic() - started, {"sweep": sweep.name})
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
