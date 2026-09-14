"""Retrying projection of durable lifecycle facts into Run presentation persistence."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import anyio
from a13n_harness import SafeFailure
from anyio import create_task_group
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.lifecycle import (
    LifecycleEvent,
    LifecycleProjectionClaim,
    claim_lifecycle_projections,
    complete_lifecycle_projection,
    fail_lifecycle_projection,
)
from a13n_service.lifecycle.persistence import has_abandoned_run_projection
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import require_aware_utc, utc_now

from .activation import PublicationActivator
from .domain import (
    CompleteRunStream,
    PublicationPending,
    RetainedReplayUnavailable,
    RunStreamEvent,
    deterministic_run_stream_event_id,
)
from .events import lifecycle_stream_event
from .redis import RedisRunStream
from .replay import RunReplayStore, project_retained_items

logger = logging.getLogger("a13n_service.run_stream.projector")
_TERMINAL_RUN_EVENTS = frozenset({"run.waiting", "run.completed", "run.failed", "run.cancelled"})
_PROJECTION_FAILURE = SafeFailure(
    code="run_stream_projection_failed",
    message="Run presentation projection is temporarily unavailable.",
    retry_hint="dependency_change",
)


class _ReplayPublicationFailed(RuntimeError):
    """A complete Redis source remains available, but object publication failed."""


class LifecycleRunStreamProjector:
    """Claim durable facts briefly, project outside SQL, then settle their leases."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        stream: RedisRunStream,
        replay: RunReplayStore,
        *,
        worker_id: str,
        lease_duration: timedelta = timedelta(seconds=30),
        retry_after: timedelta = timedelta(seconds=5),
        max_attempts: int = 20,
        poll_interval_seconds: float = 1,
        claim_limit: int = 16,
        terminal_projection: Callable[[LifecycleEvent, CompleteRunStream], Awaitable[None]] | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not worker_id:
            raise ValueError("lifecycle projection worker identity is required")
        if lease_duration <= timedelta(0) or retry_after < timedelta(0) or max_attempts < 1:
            raise ValueError("lifecycle projection retry policy is invalid")
        if poll_interval_seconds <= 0 or claim_limit < 1 or claim_limit > 200:
            raise ValueError("lifecycle projection polling policy is invalid")
        self._sessions = sessions
        self._stream = stream
        self._replay = replay
        self._worker_id = worker_id
        self._lease_duration = lease_duration
        self._retry_after = retry_after
        self._max_attempts = max_attempts
        self._poll_interval_seconds = poll_interval_seconds
        self._claim_limit = claim_limit
        self._terminal_projection = terminal_projection
        self._clock = clock
        self._activation = PublicationActivator(sessions, stream, clock=clock)

    async def run(self) -> None:
        while True:
            try:
                claimed = await self.project_once(limit=self._claim_limit)
            except anyio.get_cancelled_exc_class():
                raise
            except Exception:
                logger.exception(
                    "Run Stream projection sweep failed",
                    extra={"event": "run_stream_projection_sweep_failed", "worker_id": self._worker_id},
                )
                claimed = 0
            if claimed < self._claim_limit:
                await anyio.sleep(self._poll_interval_seconds)

    async def project_once(self, *, limit: int = 16) -> int:
        now = _utc(self._clock())
        async with transaction(self._sessions) as database:
            claims = await claim_lifecycle_projections(
                database,
                lease_owner=self._worker_id,
                now=now,
                lease_duration=self._lease_duration,
                limit=limit,
            )
        async with create_task_group() as tasks:
            for claim in claims:
                tasks.start_soon(self._project_claim, claim)
        return len(claims)

    async def _project_claim(self, claim: LifecycleProjectionClaim) -> None:
        if claim.event.event_type in _TERMINAL_RUN_EVENTS and claim.event.projection_attempts > self._max_attempts:
            # Publication retries are exhausted. The same durable claim now owns
            # only retirement, which must finish before this fact can be abandoned.
            await self._settle_failure(claim, source_complete=False, failure=_PROJECTION_FAILURE)
            return
        try:
            await self._project_event(claim.event)
        except _ReplayPublicationFailed:
            logger.exception(
                "Run replay publication failed",
                extra={"lifecycle_event_id": claim.event.id},
            )
            await self._settle_failure(
                claim,
                source_complete=True,
                failure=SafeFailure(
                    code="run_replay_publication_failed",
                    message="Retained Run presentation publication is temporarily unavailable.",
                    retry_hint="dependency_change",
                ),
            )
            return
        except Exception:
            logger.exception("Run Stream lifecycle projection failed", extra={"lifecycle_event_id": claim.event.id})
            await self._settle_failure(
                claim,
                source_complete=False,
                failure=_PROJECTION_FAILURE,
            )
            return
        async with transaction(self._sessions) as database:
            await complete_lifecycle_projection(database, claim, projected_at=_utc(self._clock()))

    async def _settle_failure(
        self,
        claim: LifecycleProjectionClaim,
        *,
        source_complete: bool,
        failure: SafeFailure,
    ) -> None:
        abandon = claim.event.projection_attempts >= self._max_attempts
        if abandon and not source_complete:
            abandon = await self._prepare_abandonment(claim.event)
        async with transaction(self._sessions) as database:
            await fail_lifecycle_projection(
                database,
                claim,
                failed_at=_utc(self._clock()),
                retry_after=self._retry_after,
                abandon=abandon,
                failure=failure,
            )

    async def _prepare_abandonment(self, event: LifecycleEvent) -> bool:
        terminal = event.event_type in _TERMINAL_RUN_EVENTS
        try:
            if terminal:
                await self._stream.retire(event.organization_id, event.run_id, closed_at=event.occurred_at)
            else:
                await self._stream.mark_lifecycle_incomplete(event.organization_id, event.run_id)
        except Exception:
            logger.exception(
                "Run Stream incomplete boundary could not be recorded",
                extra={"run_id": event.run_id, "lifecycle_event_id": event.id},
            )
            # Nonterminal abandonment is recorded in SQL so the eventual terminal
            # fact can retire the Run. That terminal cleanup must itself keep retrying.
            return not terminal
        return True

    async def _project_event(self, event: LifecycleEvent) -> None:
        if event.thread_id is None:
            raise ValueError("Run lifecycle projection requires Thread correlation")
        if event.event_type == "run.accepted":
            await self._activation.initialize(event)
            return
        if event.event_type == "run_attempt.leased":
            await self._activation.project_leased(event)
            return
        await self._append_lifecycle(event.organization_id, lifecycle_stream_event(event))
        if event.event_type not in _TERMINAL_RUN_EVENTS:
            return
        async with short_session(self._sessions) as database:
            abandoned = await has_abandoned_run_projection(database, event.organization_id, event.run_id)
        if abandoned:
            await self._stream.retire(event.organization_id, event.run_id, closed_at=event.occurred_at)
            return
        await self._interrupt_open_items(event)
        await self._stream.close(event.organization_id, event.run_id, closed_at=event.occurred_at)
        try:
            source = await self._stream.complete_source(event.organization_id, event.run_id)
        except RetainedReplayUnavailable:
            logger.info(
                "Run Stream closed without retained replay",
                extra={"run_id": event.run_id, "lifecycle_event_id": event.id},
            )
            return
        try:
            await self._replay.publish(event.organization_id, event.run_id, source)
        except RetainedReplayUnavailable:
            logger.info(
                "Run Stream closed without retained replay",
                extra={"run_id": event.run_id, "lifecycle_event_id": event.id},
            )
        except Exception as error:
            raise _ReplayPublicationFailed("retained replay object publication failed") from error
        if self._terminal_projection is not None:
            try:
                await self._terminal_projection(event, source)
            except Exception as error:
                raise _ReplayPublicationFailed("secondary retained projection publication failed") from error

    async def _interrupt_open_items(self, event: LifecycleEvent) -> None:
        if event.thread_id is None:  # pragma: no cover - guarded by the caller
            raise ValueError("Run lifecycle projection requires Thread correlation")
        try:
            entries = await self._stream.untrimmed_entries(event.organization_id, event.run_id)
        except RetainedReplayUnavailable:
            logger.info(
                "Run Stream closed without complete Item interruption projection",
                extra={"run_id": event.run_id, "lifecycle_event_id": event.id},
            )
            return
        last_entry_by_item = {entry.event.item_id: entry for entry in entries if entry.event.item_id is not None}
        for item in project_retained_items(entries):
            if item.state != "interrupted":
                continue
            source = last_entry_by_item[item.id].event
            if source.event_type == "item.interrupted":
                continue
            payload = {
                "item_kind": item.kind,
                "item_state": "interrupted",
                "first_stream_id": item.first_stream_id,
                "last_content_stream_id": item.last_stream_id,
                "interruption": {
                    "code": "run_closed_before_item_completion",
                    "message": "The Run closed before this presentation Item completed.",
                },
            }
            if item.parent_item_id is not None:
                payload["parent_item_id"] = item.parent_item_id
            await self._append_lifecycle(
                event.organization_id,
                RunStreamEvent(
                    event_id=deterministic_run_stream_event_id(
                        "item",
                        event.id,
                        item.id,
                        "interrupted",
                    ),
                    event_type="item.interrupted",
                    run_id=event.run_id,
                    thread_id=event.thread_id,
                    run_attempt_id=source.run_attempt_id,
                    harness_run_id=source.harness_run_id,
                    lifecycle_event_id=event.id,
                    item_id=item.id,
                    occurred_at=event.occurred_at,
                    payload=payload,
                ),
            )

    async def _append_lifecycle(self, organization_id: str, event: RunStreamEvent) -> None:
        try:
            await self._stream.append_lifecycle(organization_id, event)
        except PublicationPending:
            # Worker activation can overtake older facts in SQL projection order.
            # Resume that same current activation so the ordered projector can advance.
            await self._activation.resume_current(organization_id, event.run_id)
            await self._stream.append_lifecycle(organization_id, event)


def _utc(value: datetime) -> datetime:
    try:
        return require_aware_utc(value)
    except ValueError as error:
        raise ValueError("projection clock must return an offset-aware timestamp") from error


__all__ = ["LifecycleRunStreamProjector"]
