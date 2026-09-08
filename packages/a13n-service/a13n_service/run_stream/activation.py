"""Durable-authority validation around the atomic Redis publication boundary."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from a13n_logging import get_logger
from anyio import fail_after, sleep
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.interactions.attempts import AttemptAuthorityError, AttemptContext, read_attempt_authority
from a13n_service.interactions.models import RunAttemptRecord, RunRecord
from a13n_service.lifecycle import LifecycleEvent, LifecycleProjectionState
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.lifecycle.persistence import complete_publication_boundary
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import assume_utc, utc_now

from .domain import PublicationRejected, PublicationUnavailable, RecoveryReason
from .events import lifecycle_stream_event
from .redis import RedisRunStream

logger = get_logger(__name__)


class PublicationActivator:
    """Workers and lifecycle repair share event identities and one activation path."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        stream: RedisRunStream,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = sessions
        self._stream = stream
        self._clock = clock

    async def activate(self, context: AttemptContext) -> None:
        # The executor already supervises lease renewal throughout these retries.
        with fail_after(context.reconciliation_timeout.total_seconds()):
            for retry in range(3):
                try:
                    async with short_session(self._sessions) as database:
                        await read_attempt_authority(database, context, self._clock())
                        fact = await _fact(
                            database,
                            context.organization_id,
                            context.run_id,
                            "run_attempt.leased",
                            context.run_attempt_id,
                        )
                    await self.project_leased(fact, context=context)
                    return
                except PublicationRejected as error:
                    logger.info(
                        "run_stream_activation_refused",
                        extra={"run_id": context.run_id, "attempt_number": context.attempt_number},
                    )
                    raise AttemptAuthorityError("Attempt publication authority was replaced") from error
                except PublicationUnavailable:
                    logger.warning(
                        "run_stream_activation_retry",
                        extra={"run_id": context.run_id, "attempt_number": context.attempt_number, "retry": retry + 1},
                    )
                    if retry == 2:
                        raise
                    await sleep(0.05 * (2**retry))

    async def initialize(self, accepted: LifecycleEvent) -> None:
        server_id = await self._stream.server_incarnation()
        async with short_session(self._sessions) as database:
            accepted = await _fact(database, accepted.organization_id, accepted.run_id, "run.accepted")
        await self._stream.initialize(
            accepted.organization_id,
            lifecycle_stream_event(accepted),
            allow_create=_may_create(accepted),
            expected_server_id=server_id,
        )
        await self._settle(accepted)

    async def project_leased(self, fact: LifecycleEvent, *, context: AttemptContext | None = None) -> None:
        if fact.event_type != "run_attempt.leased" or fact.run_attempt_id is None:
            raise ValueError("Publication activation requires a leased lifecycle fact")
        async with short_session(self._sessions) as database:
            # Refresh projection state: an old claim cannot recreate a lost activation.
            fact = await _fact(database, fact.organization_id, fact.run_id, fact.event_type, fact.run_attempt_id)
            accepted = await _fact(database, fact.organization_id, fact.run_id, "run.accepted")
            if context is not None:
                await read_attempt_authority(database, context, self._clock())
            current = await _is_current(database, fact, self._clock())
            abandoned = await database.scalar(
                select(LifecycleEventRecord.id)
                .where(
                    LifecycleEventRecord.organization_id == fact.organization_id,
                    LifecycleEventRecord.run_id == fact.run_id,
                    LifecycleEventRecord.projection_state == LifecycleProjectionState.abandoned.value,
                )
                .limit(1)
            )
            if abandoned is not None:
                raise PublicationUnavailable("Run Stream history has an abandoned projection")
        await self.initialize(accepted)
        leased = lifecycle_stream_event(fact)
        number = fact.payload.get("attempt_number")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise ValueError("Leased lifecycle fact omitted its fencing number")
        reason = _recovery_reason(number, fact.payload.get("start_reason"))
        if current:
            result = await self._stream.activate(
                fact.organization_id,
                leased,
                attempt_number=number,
                reason=reason,
                allow_create=_may_create(fact),
            )
            if not result.active:
                raise PublicationRejected("A newer publication generation is already active")
        else:
            # A superseded claim can settle a known receipt, or a known absence.
            # Missing metadata and partial activation are explicitly inconclusive.
            await self._stream.activation_result(fact.organization_id, leased, attempt_number=number, reason=reason)
            if context is not None:
                raise PublicationRejected("Attempt is no longer current")
        await self._settle(fact)
        logger.info(
            "run_stream_activation_settled", extra={"run_id": fact.run_id, "attempt_number": number, "active": current}
        )

    async def _settle(self, fact: LifecycleEvent) -> None:
        async with transaction(self._sessions) as database:
            if not await complete_publication_boundary(database, fact, projected_at=self._clock()):
                raise PublicationUnavailable("Publication boundary was abandoned or removed")


def _may_create(fact: LifecycleEvent) -> bool:
    if fact.projection_state is LifecycleProjectionState.abandoned:
        raise PublicationUnavailable("Publication boundary was abandoned")
    return fact.projection_state is not LifecycleProjectionState.projected


async def _fact(
    database: AsyncSession,
    organization_id: str,
    run_id: str,
    event_type: str,
    attempt_id: str | None = None,
) -> LifecycleEvent:
    statement = select(LifecycleEventRecord).where(
        LifecycleEventRecord.organization_id == organization_id,
        LifecycleEventRecord.run_id == run_id,
        LifecycleEventRecord.event_type == event_type,
    )
    if attempt_id is not None:
        statement = statement.where(LifecycleEventRecord.run_attempt_id == attempt_id)
    record = await database.scalar(statement)
    if record is None:
        raise PublicationUnavailable("Committed publication boundary is unavailable")
    return record.to_resource()


async def _is_current(database: AsyncSession, fact: LifecycleEvent, now: datetime) -> bool:
    row = await database.scalar(
        select(RunAttemptRecord)
        .join(RunRecord, RunRecord.id == RunAttemptRecord.run_id)
        .where(
            RunAttemptRecord.organization_id == fact.organization_id,
            RunAttemptRecord.id == fact.run_attempt_id,
            RunRecord.organization_id == fact.organization_id,
            RunRecord.id == fact.run_id,
            RunRecord.current_run_attempt_id == RunAttemptRecord.id,
            RunRecord.status == "running",
            RunAttemptRecord.status.in_(("leased", "running")),
        )
    )
    return row is not None and assume_utc(row.lease_expires_at) > now


def _recovery_reason(number: int, reason: object) -> RecoveryReason | None:
    if number == 1:
        return None
    if reason == "attempt_failed":
        return "retry_after_failure"
    if reason in ("lease_expired", "planned_handoff", "retry_after_failure", "pending_input"):
        return reason
    raise ValueError("Successor leased fact has an unsupported recovery reason")
