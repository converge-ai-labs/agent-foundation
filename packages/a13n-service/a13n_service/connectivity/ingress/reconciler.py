"""Ordered, lease-fenced delivery of durable input batches."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import anyio
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, utc_now

from .admission_domain import (
    BatchConfiguration,
    InputAcceptanceOutcome,
    InputAcceptor,
    PreparedIngressBatch,
    RejectedInputOutcome,
    RetryableInputOutcome,
)
from .admission_models import AgentThreadBindingRecord, IngressAdmissionRecord, IngressBatchRecord
from .contributions import IngressObservations
from .input import project_events
from .provider import ExternalRef


@dataclass(frozen=True, slots=True)
class _Claim:
    batch_id: str
    binding_id: str
    generation: int
    attempt_count: int


class IngressAdmissionReconciler:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        acceptor: InputAcceptor,
        *,
        instance_id: str,
        poll_interval_seconds: float,
        lease_seconds: float,
        backoff_steps: int,
        max_backoff_seconds: float,
        input_max_bytes: int,
        clock: Clock = utc_now,
        observations: IngressObservations | None = None,
    ) -> None:
        self._sessions = sessions
        self._acceptor = acceptor
        self._instance_id = instance_id
        self._poll_interval_seconds = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._backoff_steps = backoff_steps
        self._max_backoff_seconds = max_backoff_seconds
        self._input_max_bytes = input_max_bytes
        self._clock = clock
        self._observations = observations

    async def run(self) -> None:
        while True:
            if not await self.run_once():
                await anyio.sleep(self._poll_interval_seconds)

    async def run_once(self) -> bool:
        claim = await self._claim()
        if claim is None:
            return False
        try:
            prepared = await self._prepare(claim)
        except ValueError:
            await self._complete(claim, RejectedInputOutcome(reason_code="input_invalid"))
            return True
        if prepared is None:
            return True
        try:
            outcome = await self._acceptor.accept_ingress_batch(prepared)
        except Exception:
            outcome = RetryableInputOutcome(
                reason_code="input_acceptor_unavailable",
                available_at=self._retry_at(claim.attempt_count),
            )
        await self._complete(claim, outcome)
        return True

    async def _claim(self) -> _Claim | None:
        now = self._clock()
        earlier = aliased(IngressBatchRecord)
        eligible = select(IngressBatchRecord.id).where(
            IngressBatchRecord.binding_id == AgentThreadBindingRecord.id,
            IngressBatchRecord.status == "pending",
            IngressBatchRecord.available_at <= now,
            or_(IngressBatchRecord.claim_owner.is_(None), IngressBatchRecord.claim_expires_at <= now),
            ~exists(
                select(earlier.id).where(
                    earlier.binding_id == IngressBatchRecord.binding_id,
                    earlier.status == "pending",
                    earlier.sequence < IngressBatchRecord.sequence,
                )
            ),
        )
        async with transaction(self._sessions) as session:
            # Lock the conversation first everywhere: skipping a locked batch must
            # never let a second Pod overtake it in the same conversation.
            binding = await session.scalar(
                select(AgentThreadBindingRecord)
                .where(AgentThreadBindingRecord.next_submission_at <= now, exists(eligible))
                .order_by(AgentThreadBindingRecord.next_submission_at, AgentThreadBindingRecord.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if binding is None:
                return None
            batch = await session.scalar(
                select(IngressBatchRecord)
                .where(IngressBatchRecord.binding_id == binding.id, IngressBatchRecord.status == "pending")
                .order_by(IngressBatchRecord.sequence)
                .limit(1)
                .with_for_update()
            )
            if batch is None:
                return None
            batch.claim_generation += 1
            batch.claim_owner = self._instance_id
            batch.claim_expires_at = now + timedelta(seconds=self._lease_seconds)
            batch.attempt_count += 1
            batch.updated_at = now
            return _Claim(batch.id, binding.id, batch.claim_generation, batch.attempt_count)

    async def _prepare(self, claim: _Claim) -> PreparedIngressBatch | None:
        async with short_session(self._sessions) as session:
            batch = await session.get(IngressBatchRecord, claim.batch_id)
            if (
                batch is None
                or batch.status != "pending"
                or batch.claim_owner != self._instance_id
                or batch.claim_generation != claim.generation
            ):
                return None
            binding = await session.get(AgentThreadBindingRecord, claim.binding_id)
            if binding is None:
                raise ValueError("binding_unavailable")
            admissions = tuple(
                (
                    await session.scalars(
                        select(IngressAdmissionRecord)
                        .where(IngressAdmissionRecord.batch_id == batch.id)
                        .order_by(IngressAdmissionRecord.ordering_key, IngressAdmissionRecord.id)
                    )
                ).all()
            )
            if not admissions or len(admissions) != batch.event_count:
                raise ValueError("batch_inconsistent")
            return PreparedIngressBatch(
                batch_id=batch.id,
                organization_id=batch.organization_id,
                workspace_id=batch.workspace_id,
                binding_id=binding.id,
                external_ref=ExternalRef(kind=binding.external_ref_kind, id=binding.external_ref_id),
                configuration=BatchConfiguration.model_validate(batch.configuration_json),
                claim_owner=self._instance_id,
                claim_generation=claim.generation,
                agent_input=project_events(
                    tuple(item.event_json for item in admissions), max_bytes=self._input_max_bytes
                ),
            )

    async def _complete(self, claim: _Claim, outcome: InputAcceptanceOutcome) -> None:
        # Accepted receipts are written by the canonical submission transaction.
        # A separate update here could acknowledge a Run whose acceptance rolled back.
        if outcome.kind == "accepted":
            return
        now = self._clock()
        async with transaction(self._sessions) as session:
            await session.scalar(
                select(AgentThreadBindingRecord)
                .where(AgentThreadBindingRecord.id == claim.binding_id)
                .with_for_update()
            )
            batch = await session.scalar(
                select(IngressBatchRecord)
                .where(
                    IngressBatchRecord.id == claim.batch_id,
                    IngressBatchRecord.status == "pending",
                    IngressBatchRecord.claim_owner == self._instance_id,
                    IngressBatchRecord.claim_generation == claim.generation,
                )
                .with_for_update()
            )
            if batch is None:
                return
            batch.claim_owner = None
            batch.claim_expires_at = None
            batch.updated_at = now
            if outcome.kind in {"retryable", "lost_race"}:
                batch.available_at = (
                    outcome.available_at if outcome.kind == "retryable" else self._retry_at(claim.attempt_count)
                )
                return
            assert outcome.kind == "rejected"
            batch.status = "rejected"
            batch.rejection_reason = outcome.reason_code
            batch.terminal_at = now
            if self._observations is not None:
                await self._observations.rejected(session, batch_id=batch.id, reason_code=outcome.reason_code)

    def _retry_at(self, attempt_count: int) -> datetime:
        exponent = min(max(attempt_count - 1, 0), self._backoff_steps - 1)
        delay = min(self._max_backoff_seconds, self._poll_interval_seconds * 2**exponent)
        return self._clock() + timedelta(seconds=delay)
