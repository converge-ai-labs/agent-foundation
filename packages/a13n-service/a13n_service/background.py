"""Bounded periodic execution for process-supervised domain maintenance."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from math import isfinite
from time import monotonic

import anyio
import httpx2
from sqlalchemy.exc import DBAPIError

from a13n_service.storage import ObjectStoreError

logger = logging.getLogger("a13n_service.background")


@dataclass(frozen=True, slots=True)
class Sweep:
    """Observed work in one bounded domain scan; counts are not authority."""

    examined: int = 0
    completed: int = 0
    deferred: int = 0
    failed: int = 0
    oldest_age_seconds: float | None = None


class PeriodicTask:
    """Run one domain scan at a time; the process owns supervision and cancellation."""

    def __init__(
        self,
        name: str,
        scan: Callable[[], Awaitable[Sweep]],
        *,
        interval_seconds: float,
        timeout_seconds: float,
    ) -> None:
        if not name or any(not isfinite(value) or value <= 0 for value in (interval_seconds, timeout_seconds)):
            raise ValueError("periodic tasks require a name and finite positive timing bounds")
        self.name = name
        self._scan = scan
        self._interval = interval_seconds
        self._timeout = timeout_seconds
        self.last_result: Sweep | None = None
        self.last_duration_seconds: float | None = None
        self.last_outcome: str | None = None

    async def run(self) -> None:
        while True:
            await self.run_once()
            await anyio.sleep(self._interval)

    async def run_once(self) -> None:
        started = monotonic()
        try:
            with anyio.fail_after(self._timeout):
                result = await self._scan()
        except (DBAPIError, ObjectStoreError, httpx2.HTTPError, OSError, TimeoutError) as error:
            self.last_outcome = "retry"
            self.last_duration_seconds = monotonic() - started
            logger.warning(
                "background_scan_retry",
                extra={
                    "event": "background_scan_retry",
                    "task": self.name,
                    "task_owner": "control",
                    "error_type": type(error).__name__,
                    "outcome": "retry",
                    "duration_seconds": self.last_duration_seconds,
                },
            )
        else:
            self.last_result = result
            self.last_outcome = "partial_failure" if result.failed else "deferred" if result.deferred else "completed"
            self.last_duration_seconds = monotonic() - started
            log = logger.info if result.examined or result.completed or result.failed else logger.debug
            log(
                "background_scan_completed",
                extra={
                    "event": "background_scan_completed",
                    "task": self.name,
                    "task_owner": "control",
                    "outcome": self.last_outcome,
                    "duration_seconds": self.last_duration_seconds,
                    "examined": result.examined,
                    "completed": result.completed,
                    "deferred": result.deferred,
                    "failed": result.failed,
                    "oldest_age_seconds": result.oldest_age_seconds,
                },
            )
