"""Bounded Control task that creates Webhook intents from committed facts."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from math import isfinite

from a13n_harness import SafeFailure
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.background import PeriodicTask, Sweep
from a13n_service.durable_operations.http_delivery import retry_delay_seconds
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.storage import transaction
from a13n_service.storage.relational import is_database_unavailable
from a13n_service.temporal import assume_utc, require_aware_utc, utc_now

from .dispatch import claim_hook_events, dispatch_hook_event
from .invariants import HookSubscriptionInvariantError

logger = logging.getLogger("a13n_service.hooks.dispatcher")
_DISPATCH_FAILURE = SafeFailure(
    code="hook_dispatch_failed",
    message="Webhook subscription dispatch could not complete.",
    retry_hint="dependency_change",
)


class HookDispatcher:
    """Keep each claim, all destination inserts, and completion in one transaction."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        poll_interval_seconds: float = 1,
        batch_limit: int = 16,
        max_attempts: int = 10,
        retry_base_seconds: float = 2,
        retry_max_seconds: float = 300,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if any(not isfinite(value) or value <= 0 for value in (poll_interval_seconds, retry_base_seconds)):
            raise ValueError("Hook dispatch timing bounds must be finite and positive")
        if not isfinite(retry_max_seconds) or retry_max_seconds < retry_base_seconds:
            raise ValueError("Hook dispatch maximum retry delay must cover the base delay")
        if not 1 <= batch_limit <= 100 or not 1 <= max_attempts <= 1000:
            raise ValueError("Hook dispatch batch and attempt bounds are invalid")
        self._sessions = sessions
        self._poll_interval_seconds = poll_interval_seconds
        self._batch_limit = batch_limit
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self._clock = clock

    async def run(self) -> None:
        await PeriodicTask(
            "hook_dispatch",
            self.scan,
            interval_seconds=self._poll_interval_seconds,
            timeout_seconds=30,
        ).run()

    async def scan(self) -> Sweep:
        now = require_aware_utc(self._clock())
        completed = failed = deferred = 0
        failures: list[tuple[str, str, bool]] = []
        async with transaction(self._sessions) as database:
            events = await claim_hook_events(database, now=now, limit=self._batch_limit)
            oldest_age = max(((now - assume_utc(event.created_at)).total_seconds() for event in events), default=None)
            for event in events:
                # A savepoint isolates a bad event without releasing its claim.
                # Keep these values outside ORM rollback expiration.
                seq, event_id, attempts = event.seq, event.id, event.hook_dispatch_attempts + 1
                try:
                    async with database.begin_nested():
                        await dispatch_hook_event(database, event, now=now)
                except (DBAPIError, HookSubscriptionInvariantError) as error:
                    if is_database_unavailable(error):
                        raise
                    exhausted = attempts >= self._max_attempts
                    retry_at = (
                        None
                        if exhausted
                        else now
                        + timedelta(
                            seconds=retry_delay_seconds(
                                attempts, base=self._retry_base_seconds, maximum=self._retry_max_seconds
                            )
                        )
                    )
                    await database.execute(
                        update(LifecycleEventRecord)
                        .where(LifecycleEventRecord.seq == seq)
                        .values(
                            hook_dispatch_state="failed" if exhausted else "pending",
                            hook_dispatch_attempts=attempts,
                            hook_dispatch_next_attempt_at=retry_at,
                            hook_dispatched_at=None,
                            hook_dispatch_error_json=_DISPATCH_FAILURE.model_dump(mode="json"),
                        )
                        .execution_options(synchronize_session=False)
                    )
                    failed += int(exhausted)
                    deferred += int(not exhausted)
                    failures.append((event_id, type(error).__name__, exhausted))
                else:
                    completed += 1
        for event_id, error_type, exhausted in failures:
            logger.warning(
                "hook_dispatch_failed",
                extra={
                    "event": "hook_dispatch_failed",
                    "lifecycle_event_id": event_id,
                    "error_type": error_type,
                    "retryable": not exhausted,
                },
            )
        return Sweep(
            examined=len(events),
            completed=completed,
            deferred=deferred,
            failed=failed,
            oldest_age_seconds=oldest_age,
        )
