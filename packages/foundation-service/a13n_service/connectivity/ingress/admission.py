"""Authenticate provider requests and commit durable Ingress admissions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.adapters import IngressAdapter, JsonObject
from a13n_service.connectivity.composition import AdapterRegistry
from a13n_service.iam.models import WorkspaceRecord
from a13n_service.ids import new_object_id
from a13n_service.secrets import InternalSecretError, InternalSecretService, SecretOperation
from a13n_service.storage import short_session, transaction

from ._management import require_adapter, require_ingress, secret_context
from .admission_domain import ProtectedRawRef
from .admission_models import (
    IngressAdmissionRecord,
    IngressBatchEventRecord,
    IngressBatchRecord,
)
from .errors import IngressError
from .models import IngressRecord
from .provider import (
    AdmissionReceipt,
    DurableAdmissionReceipt,
    InboundEvent,
    IrrelevantAdmissionReceipt,
    ProviderHttpResponse,
    ProviderRequest,
    ProviderRequestError,
)
from .raw_objects import IngressRawObjectStore
from .routing import EligibleRouting, IrrelevantRouting, RejectedRouting, RoutingResolution, resolve_routing


@dataclass(frozen=True, slots=True)
class _IngressSnapshot:
    id: str
    organization_id: str
    workspace_id: str
    provider_key: str
    provider_config_version: str
    provider_config_json: JsonObject
    credential_generation: int
    version: int


@dataclass(frozen=True, slots=True)
class _FrozenRoutingFields:
    route_id: str | None
    route_version: int | None
    selected_agent_id: str | None
    external_ref_kind: str | None
    external_ref_id: str | None
    binding_state: str
    binding_id: str | None
    mapping_json: dict[str, JsonValue] | None
    mapping_digest: str | None
    min_interval_ms: int
    max_batch_events: int
    provider_context_json: dict[str, JsonValue]
    provider_policy_json: dict[str, JsonValue]
    native_actions_json: list[str]
    capability_overlay_json: dict[str, JsonValue] | None
    compatibility_digest: str


class IngressEventService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        adapters: AdapterRegistry[IngressAdapter],
        secrets: InternalSecretService,
        raw_objects: IngressRawObjectStore,
        *,
        request_max_bytes: int,
        raw_retention_seconds: int,
        workspace_pending_max_count: int,
        workspace_pending_max_bytes: int,
        ingress_pending_max_count: int,
        ingress_pending_max_bytes: int,
        batch_max_bytes: int,
        dedup_horizon_seconds: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._adapters = adapters
        self._secrets = secrets
        self._raw_objects = raw_objects
        self._request_max_bytes = request_max_bytes
        self._raw_retention_seconds = raw_retention_seconds
        self._workspace_pending_max_count = workspace_pending_max_count
        self._workspace_pending_max_bytes = workspace_pending_max_bytes
        self._ingress_pending_max_count = ingress_pending_max_count
        self._ingress_pending_max_bytes = ingress_pending_max_bytes
        self._batch_max_bytes = batch_max_bytes
        self._dedup_horizon_seconds = dedup_horizon_seconds
        self._clock = clock

    async def receive(self, *, ingress_id: str, request: ProviderRequest) -> ProviderHttpResponse:
        snapshot, adapter, credentials = await self._load_runtime(ingress_id)
        if len(request.body) > min(self._request_max_bytes, adapter.max_request_bytes):
            return adapter.failure_response("request_too_large")
        received_at = self._clock()
        try:
            decision = await adapter.authenticate_and_normalize(
                request,
                ingress_id=ingress_id,
                ingress_config=snapshot.provider_config_json,
                credentials=credentials,
                received_at=received_at,
            )
        except ProviderRequestError as error:
            return error.response
        if decision.kind == "complete":
            return decision.response
        event = decision.event
        identity_digest = _identity_digest(event)
        raw_ref = None
        try:
            if event.retain_raw and self._raw_retention_seconds > 0:
                raw_ref = await self._raw_objects.retain(
                    organization_id=snapshot.organization_id,
                    workspace_id=snapshot.workspace_id,
                    ingress_id=ingress_id,
                    identity_digest=identity_digest,
                    body=request.body,
                    expires_at=received_at + timedelta(seconds=self._raw_retention_seconds),
                )
            receipt = await self._admit(
                snapshot=snapshot,
                adapter=adapter,
                event=event,
                identity_digest=identity_digest,
                request_digest=hashlib.sha256(request.body).hexdigest(),
                raw_ref=raw_ref,
            )
        except IngressError as error:
            if raw_ref is not None:
                await self._raw_objects.delete(raw_ref)
            return adapter.failure_response(error.code)
        if receipt.admission_id is None and raw_ref is not None:
            await self._raw_objects.delete(raw_ref)
        return adapter.acknowledge(receipt)

    async def _load_runtime(self, ingress_id: str) -> tuple[_IngressSnapshot, IngressAdapter, JsonObject]:
        async with short_session(self._sessions) as session:
            try:
                record = await require_ingress(session, ingress_id)
            except IngressError as error:
                raise IngressError("ingress_not_found", "Ingress was not found.", status_code=404) from error
            snapshot = _snapshot(record)
        adapter = require_adapter(self._adapters, snapshot.provider_key, snapshot.provider_config_version)
        try:
            value = await self._secrets.resolve(
                secret_context(
                    organization_id=snapshot.organization_id,
                    workspace_id=snapshot.workspace_id,
                    ingress_id=snapshot.id,
                    generation=snapshot.credential_generation,
                    operation=SecretOperation.runtime,
                )
            )
            credentials = json.loads(value)
            if not isinstance(credentials, dict) or any(not isinstance(key, str) for key in credentials):
                raise ValueError("invalid credential bundle")
        except (InternalSecretError, ValueError, json.JSONDecodeError) as error:
            raise IngressError("ingress_not_found", "Ingress was not found.", status_code=404) from error
        return snapshot, adapter, credentials

    async def _admit(
        self,
        *,
        snapshot: _IngressSnapshot,
        adapter: IngressAdapter,
        event: InboundEvent,
        identity_digest: str,
        request_digest: str,
        raw_ref: ProtectedRawRef | None,
    ) -> AdmissionReceipt:
        now = self._clock()
        async with transaction(self._sessions) as session:
            await _lock_workspace(session, snapshot.workspace_id)
            ingress = await require_ingress(session, snapshot.id, lock=True)
            if (
                ingress.version != snapshot.version
                or ingress.credential_generation != snapshot.credential_generation
                or ingress.provider_config_version != snapshot.provider_config_version
            ):
                raise IngressError("ingress_changed", "Ingress changed during authentication.", status_code=503)
            duplicate = await session.scalar(
                select(IngressAdmissionRecord).where(
                    IngressAdmissionRecord.ingress_id == ingress.id,
                    IngressAdmissionRecord.event_identity_kind == event.identity_kind,
                    IngressAdmissionRecord.event_identity_digest == identity_digest,
                )
            )
            if duplicate is not None:
                if duplicate.request_digest != request_digest:
                    raise IngressError(
                        "delivery_identity_conflict",
                        "Provider delivery identity was reused with different content.",
                        status_code=409,
                    )
                return _receipt(duplicate, duplicate=True)

            routing = await resolve_routing(session, adapter=adapter, ingress=ingress, event=event)
            if isinstance(routing, IrrelevantRouting):
                return IrrelevantAdmissionReceipt(
                    reason_code=routing.reason_code,
                )
            event_json = event.model_dump(mode="json", exclude={"external_event_id", "retain_raw"})
            event_size = len(_canonical(event_json))
            frozen_routing = _freeze_routing(routing, fallback_digest=identity_digest)
            if isinstance(routing, RejectedRouting):
                rejected = _admission_record(
                    ingress=ingress,
                    event=event,
                    event_json=event_json,
                    event_size=event_size,
                    identity_digest=identity_digest,
                    request_digest=request_digest,
                    raw_ref=raw_ref,
                    routing=frozen_routing,
                    now=now,
                    status="rejected",
                    dedup_horizon_seconds=_dedup_horizon(adapter, self._dedup_horizon_seconds),
                )
                rejected.rejection_reason = routing.reason_code
                rejected.terminal_at = now
                session.add(rejected)
                await session.flush()
                return _receipt(rejected, duplicate=False)

            await self._require_capacity(session, ingress, event_size)
            admission = _admission_record(
                ingress=ingress,
                event=event,
                event_json=event_json,
                event_size=event_size,
                identity_digest=identity_digest,
                request_digest=request_digest,
                raw_ref=raw_ref,
                routing=frozen_routing,
                now=now,
                status="pending",
                dedup_horizon_seconds=_dedup_horizon(adapter, self._dedup_horizon_seconds),
            )
            session.add(admission)
            await session.flush()
            batch = await self._select_batch(session, ingress, routing, frozen_routing, event_size, now)
            session.add(
                IngressBatchEventRecord(
                    batch_id=batch.id,
                    admission_id=admission.id,
                    organization_id=ingress.organization_id,
                    workspace_id=ingress.workspace_id,
                    ordering_key=event.ordering_key,
                )
            )
            await session.flush()
            return _receipt(admission, duplicate=False)

    async def _require_capacity(self, session: AsyncSession, ingress: IngressRecord, event_size: int) -> None:
        workspace_count, workspace_bytes = (
            await session.execute(
                select(func.count(), func.coalesce(func.sum(IngressAdmissionRecord.event_size_bytes), 0)).where(
                    IngressAdmissionRecord.workspace_id == ingress.workspace_id,
                    IngressAdmissionRecord.status == "pending",
                )
            )
        ).one()
        ingress_count, ingress_bytes = (
            await session.execute(
                select(func.count(), func.coalesce(func.sum(IngressAdmissionRecord.event_size_bytes), 0)).where(
                    IngressAdmissionRecord.ingress_id == ingress.id,
                    IngressAdmissionRecord.status == "pending",
                )
            )
        ).one()
        if (
            workspace_count + 1 > self._workspace_pending_max_count
            or workspace_bytes + event_size > self._workspace_pending_max_bytes
            or ingress_count + 1 > self._ingress_pending_max_count
            or ingress_bytes + event_size > self._ingress_pending_max_bytes
        ):
            raise IngressError("admission_capacity_exhausted", "Ingress admission capacity is full.", status_code=503)

    async def _select_batch(
        self,
        session: AsyncSession,
        ingress: IngressRecord,
        routing: EligibleRouting,
        frozen_routing: _FrozenRoutingFields,
        event_size: int,
        now: datetime,
    ) -> IngressBatchRecord:
        candidate = await session.scalar(
            select(IngressBatchRecord)
            .where(
                IngressBatchRecord.ingress_id == ingress.id,
                IngressBatchRecord.compatibility_digest == frozen_routing.compatibility_digest,
                IngressBatchRecord.status == "pending",
                IngressBatchRecord.claim_owner.is_(None),
                IngressBatchRecord.append_until >= now,
                IngressBatchRecord.event_count < routing.max_batch_events,
            )
            .order_by(IngressBatchRecord.created_at.desc(), IngressBatchRecord.id.desc())
            .with_for_update()
        )
        if candidate is not None and candidate.event_bytes + event_size <= self._batch_max_bytes:
            candidate.event_count += 1
            candidate.event_bytes += event_size
            candidate.updated_at = now
            return candidate
        batch = IngressBatchRecord(
            id=new_object_id("ibat"),
            organization_id=ingress.organization_id,
            workspace_id=ingress.workspace_id,
            ingress_id=ingress.id,
            compatibility_digest=frozen_routing.compatibility_digest,
            status="pending",
            event_count=1,
            event_bytes=event_size,
            append_until=now + timedelta(milliseconds=routing.min_interval_ms),
            available_at=now,
            attempt_count=0,
            claim_generation=0,
            claim_owner=None,
            claim_expires_at=None,
            result_kind=None,
            result_id=None,
            rejection_reason=None,
            created_at=now,
            updated_at=now,
            terminal_at=None,
        )
        session.add(batch)
        await session.flush()
        return batch


def _admission_record(
    *,
    ingress: IngressRecord,
    event: InboundEvent,
    event_json: dict[str, object],
    event_size: int,
    identity_digest: str,
    request_digest: str,
    raw_ref: ProtectedRawRef | None,
    routing: _FrozenRoutingFields,
    now: datetime,
    status: str,
    dedup_horizon_seconds: int,
) -> IngressAdmissionRecord:
    return IngressAdmissionRecord(
        id=new_object_id("iadm"),
        organization_id=ingress.organization_id,
        workspace_id=ingress.workspace_id,
        ingress_id=ingress.id,
        ingress_version=ingress.version,
        provider_key=ingress.provider_key,
        provider_config_version=ingress.provider_config_version,
        route_id=routing.route_id,
        route_version=routing.route_version,
        event_identity_kind=event.identity_kind,
        event_identity_digest=identity_digest,
        protected_event_identity=event.external_event_id,
        request_digest=request_digest,
        event_type=event.type,
        normalization_version=event.normalization_version,
        event_json=event_json,
        ordering_key=event.ordering_key,
        raw_ref_json=raw_ref.model_dump(mode="json") if raw_ref is not None else None,
        selected_agent_id=routing.selected_agent_id,
        external_ref_kind=routing.external_ref_kind,
        external_ref_id=routing.external_ref_id,
        binding_state=routing.binding_state,
        binding_id=routing.binding_id,
        mapping_json=routing.mapping_json,
        mapping_digest=routing.mapping_digest,
        min_interval_ms=routing.min_interval_ms,
        max_batch_events=routing.max_batch_events,
        provider_context_json=routing.provider_context_json,
        provider_policy_json=routing.provider_policy_json,
        native_actions_json=routing.native_actions_json,
        capability_overlay_json=routing.capability_overlay_json,
        compatibility_digest=routing.compatibility_digest,
        event_size_bytes=event_size,
        status=status,
        result_kind=None,
        result_id=None,
        rejection_reason=None,
        available_at=now,
        attempt_count=0,
        claim_generation=0,
        claim_owner=None,
        claim_expires_at=None,
        dedup_expires_at=now + timedelta(seconds=dedup_horizon_seconds),
        terminal_at=None,
        created_at=now,
        updated_at=now,
    )


def _receipt(record: IngressAdmissionRecord, *, duplicate: bool) -> DurableAdmissionReceipt:
    return DurableAdmissionReceipt.model_validate(
        {
            "admission_id": record.id,
            "status": record.status,
            "duplicate": duplicate,
            "reason_code": record.rejection_reason,
        }
    )


def _freeze_routing(routing: RoutingResolution, *, fallback_digest: str) -> _FrozenRoutingFields:
    if isinstance(routing, EligibleRouting):
        route = routing.route
        return _FrozenRoutingFields(
            route_id=route.id if route is not None else None,
            route_version=route.version if route is not None else None,
            selected_agent_id=routing.selected_agent_id,
            external_ref_kind=routing.external_ref.kind,
            external_ref_id=routing.external_ref.id,
            binding_state=routing.binding_state.value,
            binding_id=routing.binding_id,
            mapping_json=routing.mapping.value,
            mapping_digest=routing.mapping.digest,
            min_interval_ms=routing.min_interval_ms,
            max_batch_events=routing.max_batch_events,
            provider_context_json=routing.provider_context,
            provider_policy_json=routing.provider_policy,
            native_actions_json=list(routing.native_actions),
            capability_overlay_json=routing.capability_overlay,
            compatibility_digest=routing.compatibility_digest,
        )
    if isinstance(routing, RejectedRouting):
        route = routing.route
        return _FrozenRoutingFields(
            route_id=route.id if route is not None else None,
            route_version=route.version if route is not None else None,
            selected_agent_id=None,
            external_ref_kind=None,
            external_ref_id=None,
            binding_state="unbound",
            binding_id=None,
            mapping_json=None,
            mapping_digest=None,
            min_interval_ms=1,
            max_batch_events=1,
            provider_context_json={},
            provider_policy_json={},
            native_actions_json=[],
            capability_overlay_json=None,
            compatibility_digest=fallback_digest,
        )
    raise TypeError("irrelevant routing decisions are not durable")


async def _lock_workspace(session: AsyncSession, workspace_id: str) -> None:
    workspace = await session.scalar(
        select(WorkspaceRecord).where(WorkspaceRecord.id == workspace_id).with_for_update()
    )
    if workspace is None:
        raise IngressError("ingress_not_found", "Ingress was not found.", status_code=404)


def _identity_digest(event: InboundEvent) -> str:
    value = f"a13n-ingress-event-v1\0{event.identity_kind}\0{event.external_event_id}".encode()
    return hashlib.sha256(value).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _snapshot(record: IngressRecord) -> _IngressSnapshot:
    return _IngressSnapshot(
        id=record.id,
        organization_id=record.organization_id,
        workspace_id=record.workspace_id,
        provider_key=record.provider_key,
        provider_config_version=record.provider_config_version,
        provider_config_json=dict(record.provider_config_json),
        credential_generation=record.credential_generation,
        version=record.version,
    )


def _dedup_horizon(adapter: IngressAdapter, deployment_max: int) -> int:
    if adapter.dedup_horizon_seconds <= 0:
        raise IngressError("invalid_adapter_contract", "Ingress adapter contract is invalid.", status_code=503)
    return min(adapter.dedup_horizon_seconds, deployment_max)
