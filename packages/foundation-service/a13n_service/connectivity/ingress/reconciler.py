"""Lease-fenced reconciliation of durable Ingress batches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import anyio
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam.authorization import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_agent,
)
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, utc_now

from .admission_domain import (
    BindingState,
    InputAcceptanceOutcome,
    InputAcceptor,
    PreparedIngressBatch,
    RejectedInputOutcome,
    RetryableInputOutcome,
)
from .admission_models import (
    AgentThreadBindingRecord,
    IngressAdmissionRecord,
    IngressBatchEventRecord,
    IngressBatchRecord,
)
from .mapping import EventInputBatch, EventInputView, MappingError, compile_mapping
from .models import IngressAgentRecord, IngressRecord, RouteRecord
from .provider import ExternalRef

_EVENT_VIEW = TypeAdapter(EventInputView)


@dataclass(frozen=True, slots=True)
class _Claim:
    batch_id: str
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
        max_attempts: int,
        max_backoff_seconds: float,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._acceptor = acceptor
        self._instance_id = instance_id
        self._poll_interval_seconds = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._max_backoff_seconds = max_backoff_seconds
        self._clock = clock

    async def run(self) -> None:
        while True:
            if not await self.run_once():
                await anyio.sleep(self._poll_interval_seconds)

    async def run_once(self) -> bool:
        claim = await self._claim()
        if claim is None:
            return False
        try:
            prepared = await self._prepare(claim.batch_id)
        except _PermanentRejection as error:
            await self._complete(claim, RejectedInputOutcome(reason_code=error.reason_code))
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
        async with transaction(self._sessions) as session:
            record = await session.scalar(
                select(IngressBatchRecord)
                .where(
                    IngressBatchRecord.status == "pending",
                    IngressBatchRecord.available_at <= now,
                    or_(
                        IngressBatchRecord.claim_owner.is_(None),
                        IngressBatchRecord.claim_expires_at <= now,
                    ),
                )
                .order_by(IngressBatchRecord.available_at, IngressBatchRecord.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if record is None:
                return None
            record.claim_generation += 1
            record.claim_owner = self._instance_id
            record.claim_expires_at = now + timedelta(seconds=self._lease_seconds)
            record.attempt_count += 1
            record.updated_at = now
            return _Claim(record.id, record.claim_generation, record.attempt_count)

    async def _prepare(self, batch_id: str) -> PreparedIngressBatch:
        async with short_session(self._sessions) as session:
            batch = await session.get(IngressBatchRecord, batch_id)
            if batch is None or batch.status != "pending":
                raise _PermanentRejection("batch_unavailable")
            admissions = tuple(
                (
                    await session.scalars(
                        select(IngressAdmissionRecord)
                        .join(
                            IngressBatchEventRecord,
                            IngressBatchEventRecord.admission_id == IngressAdmissionRecord.id,
                        )
                        .where(IngressBatchEventRecord.batch_id == batch_id)
                        .order_by(IngressBatchEventRecord.ordering_key, IngressAdmissionRecord.id)
                    )
                ).all()
            )
            if not admissions or len(admissions) != batch.event_count:
                raise _PermanentRejection("batch_inconsistent")
            first = admissions[0]
            if any(item.compatibility_digest != first.compatibility_digest for item in admissions):
                raise _PermanentRejection("batch_inconsistent")
            ingress = await _reauthorize(session, first)
            if first.mapping_json is None or first.mapping_digest is None:
                raise _PermanentRejection("mapping_invalid")
            try:
                mapping = compile_mapping(first.mapping_json)
                if mapping.digest != first.mapping_digest:
                    raise _PermanentRejection("mapping_invalid")
                views = tuple(_event_view(item.event_json) for item in admissions)
                agent_input = mapping.evaluate(EventInputBatch(events=views))
            except (MappingError, ValidationError) as error:
                raise _PermanentRejection("mapping_invalid") from error
            if first.selected_agent_id is None or first.external_ref_kind is None or first.external_ref_id is None:
                raise _PermanentRejection("routing_invalid")
            return PreparedIngressBatch(
                batch_id=batch.id,
                organization_id=batch.organization_id,
                workspace_id=batch.workspace_id,
                ingress_id=batch.ingress_id,
                account_id=ingress.account_id,
                ingress_version=first.ingress_version,
                execution_service_account_id=ingress.execution_service_account_id,
                provider_key=first.provider_key,
                provider_config_version=first.provider_config_version,
                provider_context_version=first.normalization_version,
                route_id=first.route_id,
                route_version=first.route_version,
                selected_agent_id=first.selected_agent_id,
                external_ref=ExternalRef(kind=first.external_ref_kind, id=first.external_ref_id),
                binding_state=BindingState(first.binding_state),
                binding_id=first.binding_id,
                mapping_digest=first.mapping_digest,
                provider_context=first.provider_context_json,
                provider_policy=first.provider_policy_json,
                native_actions=tuple(first.native_actions_json),
                capability_overlay=first.capability_overlay_json,
                agent_input=agent_input,
                admission_ids=tuple(item.id for item in admissions),
            )

    async def _complete(self, claim: _Claim, outcome: InputAcceptanceOutcome) -> None:
        now = self._clock()
        async with transaction(self._sessions) as session:
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
            if outcome.kind in {"retryable", "lost_race"}:
                batch.available_at = outcome.available_at if outcome.kind == "retryable" else now
                batch.claim_owner = None
                batch.claim_expires_at = None
                batch.updated_at = now
                return
            batch.status = outcome.kind
            batch.result_kind = outcome.receipt_kind if outcome.kind == "accepted" else None
            batch.result_id = outcome.receipt_id if outcome.kind == "accepted" else None
            batch.rejection_reason = outcome.reason_code if outcome.kind == "rejected" else None
            batch.claim_owner = None
            batch.claim_expires_at = None
            batch.terminal_at = now
            batch.updated_at = now
            admissions = tuple(
                (
                    await session.scalars(
                        select(IngressAdmissionRecord)
                        .join(
                            IngressBatchEventRecord,
                            IngressBatchEventRecord.admission_id == IngressAdmissionRecord.id,
                        )
                        .where(IngressBatchEventRecord.batch_id == batch.id)
                        .with_for_update()
                    )
                ).all()
            )
            for admission in admissions:
                admission.status = outcome.kind
                admission.result_kind = outcome.receipt_kind if outcome.kind == "accepted" else None
                admission.result_id = outcome.receipt_id if outcome.kind == "accepted" else None
                admission.rejection_reason = outcome.reason_code if outcome.kind == "rejected" else None
                admission.terminal_at = now
                admission.updated_at = now

    def _retry_at(self, attempt_count: int) -> datetime:
        exponent = min(max(attempt_count - 1, 0), self._max_attempts - 1)
        delay = min(self._max_backoff_seconds, self._poll_interval_seconds * 2**exponent)
        return self._clock() + timedelta(seconds=delay)


class _PermanentRejection(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _event_view(value: dict[str, object]) -> EventInputView:
    return _EVENT_VIEW.validate_python(
        {
            "type": value.get("type"),
            "occurred_at": _optional_datetime(value.get("occurred_at")),
            "text": value.get("text"),
            "actor": value.get("actor"),
            "context": value.get("context"),
            "data": value.get("data"),
        }
    )


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _PermanentRejection("event_invalid")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise _PermanentRejection("event_invalid") from error


async def _reauthorize(session: AsyncSession, admission: IngressAdmissionRecord) -> IngressRecord:
    ingress = await session.get(IngressRecord, admission.ingress_id)
    if (
        ingress is None
        or ingress.status != "active"
        or ingress.account.status != "active"
        or ingress.account.deleted_at is not None
    ):
        raise _PermanentRejection("ingress_inactive")
    allowed = await session.scalar(
        select(IngressAgentRecord).where(
            IngressAgentRecord.ingress_id == ingress.id,
            IngressAgentRecord.agent_id == admission.selected_agent_id,
        )
    )
    if allowed is None or admission.selected_agent_id is None:
        raise _PermanentRejection("agent_unavailable")
    if admission.route_id is not None:
        route = await session.get(RouteRecord, admission.route_id)
        if route is None or not route.enabled:
            raise _PermanentRejection("route_inactive")
        if route.version != admission.route_version:
            raise _PermanentRejection("route_changed")
    if admission.binding_id is not None:
        binding = await session.get(AgentThreadBindingRecord, admission.binding_id)
        if (
            binding is None
            or binding.agent_id != admission.selected_agent_id
            or binding.external_ref_kind != admission.external_ref_kind
            or binding.external_ref_id != admission.external_ref_id
        ):
            raise _PermanentRejection("binding_changed")
    actor = AuthenticatedActor(
        principal=PrincipalRef(
            principal_type=PrincipalType.service_account,
            principal_id=ingress.execution_service_account_id,
        ),
        auth_method="internal",
        credential_id="connectivity-admission",
        boundary_workspace_id=ingress.workspace_id,
    )
    try:
        await authorize_agent(
            session,
            actor=actor,
            workspace_id=ingress.workspace_id,
            agent_id=admission.selected_agent_id,
            action=WorkspaceAction.agent_invoke,
        )
    except AuthorizationError as error:
        raise _PermanentRejection("execution_principal_unauthorized") from error
    return ingress
