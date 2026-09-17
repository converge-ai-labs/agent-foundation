"""Authenticate bounded provider requests and atomically append durable events."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.accounts.queries import require_account
from a13n_service.connectivity.adapters import IngressAdapter, JsonObject
from a13n_service.connectivity.composition import AdapterRegistry
from a13n_service.connectivity.errors import NativeError
from a13n_service.connectivity.management import canonical_json
from a13n_service.connectivity.native_management import require_adapter
from a13n_service.connectivity.transports.configuration import connection_key
from a13n_service.connectivity.transports.leases import ConnectionClaim, require_claim
from a13n_service.iam.models import WorkspaceRecord
from a13n_service.ids import new_object_id
from a13n_service.secrets import SecretProtectionError, SecretProtector
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .admission_domain import BatchConfiguration
from .admission_models import AgentThreadBindingRecord, IngressAdmissionRecord, IngressBatchRecord
from .contributions import IngressObservations
from .provider import (
    AdmissionReceipt,
    DurableAdmissionReceipt,
    InboundEvent,
    IrrelevantAdmissionReceipt,
    ProviderHttpResponse,
    ProviderRequest,
    ProviderRequestDecision,
    ProviderRequestError,
)
from .routing import EligibleRouting, IrrelevantRouting, resolve_routing


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    id: str
    workspace_id: str
    version: int
    credential_generation: int
    provider_key: str
    provider_config_version: str
    provider_config: JsonObject


class IngressEventService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        adapters: AdapterRegistry[IngressAdapter],
        protector: SecretProtector,
        *,
        request_max_bytes: int,
        workspace_pending_max_count: int,
        workspace_pending_max_bytes: int,
        account_pending_max_count: int,
        account_pending_max_bytes: int,
        batch_max_bytes: int,
        dedup_horizon_seconds: int,
        clock: Clock = utc_now,
        observations: IngressObservations | None = None,
    ) -> None:
        self._sessions = sessions
        self._adapters = adapters
        self._protector = protector
        self._request_max_bytes = request_max_bytes
        self._workspace_pending_max_count = workspace_pending_max_count
        self._workspace_pending_max_bytes = workspace_pending_max_bytes
        self._account_pending_max_count = account_pending_max_count
        self._account_pending_max_bytes = account_pending_max_bytes
        self._batch_max_bytes = batch_max_bytes
        self._dedup_horizon_seconds = dedup_horizon_seconds
        self._clock = clock
        self._observations = observations

    async def receive(self, *, account_id: str, request: ProviderRequest) -> ProviderHttpResponse:
        snapshot, adapter, credentials = await self.load_runtime(account_id)
        if len(request.body) > min(self._request_max_bytes, adapter.max_request_bytes):
            return adapter.failure_response("request_too_large")
        try:
            decision = await adapter.authenticate_and_normalize(
                request,
                account_id=account_id,
                account_config=snapshot.provider_config,
                credentials=credentials,
                received_at=self._clock(),
            )
        except ProviderRequestError as error:
            return error.response
        if decision.kind == "complete":
            return decision.response
        try:
            receipt = await self.admit_authenticated(
                snapshot=snapshot,
                adapter=adapter,
                event=decision.event,
                request_digest=hashlib.sha256(request.body).hexdigest(),
            )
        except NativeError as error:
            return adapter.failure_response(error.code)
        return adapter.acknowledge(receipt)

    async def receive_socket(
        self, *, snapshot: AccountSnapshot, decision: ProviderRequestDecision, claim: ConnectionClaim
    ) -> None:
        """Admit authenticated app-socket events; success permits a provider ACK."""
        if (
            snapshot.provider_config.get("event_transport") != "websocket"
            or connection_key(snapshot.provider_key, snapshot.provider_config) != claim.key
        ):
            raise NativeError(
                "connection_scope_mismatch",
                "Event connection does not own this account.",
                category=ErrorCategory.forbidden,
            )
        adapter = require_adapter(self._adapters, snapshot.provider_key, snapshot.provider_config_version)
        if decision.kind == "event":
            await self.admit_authenticated(
                snapshot=snapshot,
                adapter=adapter,
                event=decision.event,
                request_digest=hashlib.sha256(
                    canonical_json(decision.event.model_dump(mode="json", exclude={"received_at"})).encode()
                ).hexdigest(),
                claim=claim,
            )
        else:
            async with transaction(self._sessions) as session:
                await require_claim(session, claim)

    async def load_socket_account(self, account_id: str) -> tuple[AccountSnapshot, JsonObject]:
        snapshot, _, credentials = await self.load_runtime(account_id)
        return snapshot, credentials

    async def load_runtime(self, account_id: str) -> tuple[AccountSnapshot, IngressAdapter, JsonObject]:
        async with short_session(self._sessions) as session:
            account = await require_account(session, account_id)
            if account.status != "active":
                raise NativeError("account_unavailable", "Account is unavailable.", category=ErrorCategory.not_found)
            snapshot = AccountSnapshot(
                account.id,
                account.workspace_id,
                account.version,
                account.credential_generation,
                account.provider_key,
                account.provider_config_version,
                dict(account.provider_config_json),
            )
            credential = account.credential_snapshot()
        adapter = require_adapter(self._adapters, snapshot.provider_key, snapshot.provider_config_version)
        try:
            credentials = json.loads(credential.decrypt(self._protector))
            if not isinstance(credentials, dict):
                raise ValueError("invalid credentials")
        except (SecretProtectionError, ValueError) as error:
            raise NativeError(
                "account_unavailable", "Account credentials are unavailable.", category=ErrorCategory.unavailable
            ) from error
        return snapshot, adapter, credentials

    async def admit_authenticated(
        self,
        *,
        snapshot: AccountSnapshot,
        adapter: IngressAdapter,
        event: InboundEvent,
        request_digest: str,
        claim: ConnectionClaim | None = None,
        fence: Callable[[AsyncSession], Awaitable[None]] | None = None,
    ) -> AdmissionReceipt:
        now = self._clock()
        identity = hashlib.sha256(event.external_event_id.encode()).hexdigest()
        async with transaction(self._sessions) as session:
            if claim is not None:
                await require_claim(session, claim)
            # The existing Workspace capacity row also serializes same-event first
            # admission. No session or lock spans provider authentication or I/O.
            workspace = await session.scalar(
                select(WorkspaceRecord).where(WorkspaceRecord.id == snapshot.workspace_id).with_for_update()
            )
            if workspace is None or workspace.deleted_at is not None:
                raise NativeError("workspace_unavailable", "Workspace is unavailable.", category=ErrorCategory.conflict)
            account = await require_account(session, snapshot.id, lock=True)
            if (
                account.status != "active"
                or account.version != snapshot.version
                or account.credential_generation != snapshot.credential_generation
            ):
                raise NativeError(
                    "account_changed", "Account changed during authentication.", category=ErrorCategory.unavailable
                )
            if fence is not None:
                await fence(session)
            duplicate = await session.scalar(
                select(IngressAdmissionRecord).where(
                    IngressAdmissionRecord.account_id == account.id,
                    IngressAdmissionRecord.event_identity_kind == event.identity_kind,
                    IngressAdmissionRecord.event_identity_digest == identity,
                )
            )
            if duplicate is not None:
                same_message = snapshot.provider_key in {"slack", "lark"} and {
                    key: value for key, value in duplicate.event_json.items() if key != "received_at"
                } == event.model_dump(mode="json", exclude={"external_event_id", "received_at"})
                if duplicate.request_digest != request_digest and not same_message:
                    raise NativeError(
                        "delivery_identity_conflict",
                        "Delivery identity was reused with different content.",
                        category=ErrorCategory.conflict,
                    )
                return await _receipt(session, duplicate, duplicate=True)
            routing = await resolve_routing(session, adapter=adapter, account=account, event=event)
            if isinstance(routing, IrrelevantRouting):
                if self._observations is not None:
                    kind, identifier = adapter.event_target(event)
                    await self._observations.ignored(
                        session,
                        account=account,
                        event=event,
                        target_kind=kind,
                        external_target_id=identifier,
                        reason_code=routing.reason_code,
                        now=now,
                    )
                return IrrelevantAdmissionReceipt(reason_code=routing.reason_code)
            value = event.model_dump(mode="json", exclude={"external_event_id"})
            size = len(canonical_json(value).encode())
            if size > self._batch_max_bytes:
                raise NativeError("event_too_large", "Event exceeds batch capacity.", category=ErrorCategory.size_limit)
            await self._require_capacity(session, account, size)
            binding = routing.binding
            if binding is None:
                binding = AgentThreadBindingRecord(
                    id=new_object_id("bind"),
                    organization_id=account.organization_id,
                    workspace_id=account.workspace_id,
                    account_id=account.id,
                    external_ref_kind=routing.external_ref.kind,
                    external_ref_id=routing.external_ref.id,
                    thread_id=None,
                    next_batch_sequence=1,
                    next_submission_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(binding)
                await session.flush()
            batch = await self._batch(session, binding, routing, size, now)
            record = IngressAdmissionRecord(
                id=new_object_id("iadm"),
                organization_id=account.organization_id,
                workspace_id=account.workspace_id,
                account_id=account.id,
                batch_id=batch.id,
                event_identity_kind=event.identity_kind,
                event_identity_digest=identity,
                request_digest=request_digest,
                event_json=value,
                ordering_key=event.ordering_key,
                event_size_bytes=size,
                rejection_reason=None,
                dedup_expires_at=now
                + timedelta(seconds=min(adapter.dedup_horizon_seconds, self._dedup_horizon_seconds)),
                created_at=now,
            )
            session.add(record)
            await session.flush()
            if self._observations is not None:
                await self._observations.admitted(
                    session,
                    account=account,
                    configuration=routing.configuration,
                    event=event,
                    admission_id=record.id,
                    batch_id=batch.id,
                    binding_id=binding.id,
                    now=now,
                )
            return DurableAdmissionReceipt(admission_id=record.id, status="pending", duplicate=False)

    async def _require_capacity(self, session: AsyncSession, account: AccountRecord, size: int) -> None:
        pending = (
            select(func.count(), func.coalesce(func.sum(IngressAdmissionRecord.event_size_bytes), 0))
            .join(IngressBatchRecord, IngressBatchRecord.id == IngressAdmissionRecord.batch_id)
            .where(IngressBatchRecord.status == "pending", IngressAdmissionRecord.workspace_id == account.workspace_id)
        )
        workspace_count, workspace_bytes = (await session.execute(pending)).one()
        account_count, account_bytes = (
            await session.execute(pending.where(IngressAdmissionRecord.account_id == account.id))
        ).one()
        if (
            workspace_count + 1 > self._workspace_pending_max_count
            or workspace_bytes + size > self._workspace_pending_max_bytes
            or account_count + 1 > self._account_pending_max_count
            or account_bytes + size > self._account_pending_max_bytes
        ):
            raise NativeError(
                "admission_capacity_exhausted",
                "Account admission capacity is full.",
                category=ErrorCategory.unavailable,
            )

    async def _batch(
        self,
        session: AsyncSession,
        binding: AgentThreadBindingRecord,
        routing: EligibleRouting,
        size: int,
        now: datetime,
    ) -> IngressBatchRecord:
        latest = await session.scalar(
            select(IngressBatchRecord)
            .where(IngressBatchRecord.binding_id == binding.id)
            .order_by(IngressBatchRecord.sequence.desc())
            .limit(1)
            .with_for_update()
        )
        config = routing.configuration
        if (
            latest is not None
            and latest.status == "pending"
            and latest.claim_generation == 0
            and assume_utc(latest.append_until) >= now
            and latest.event_count < config.input_batching.max_batch_events
            and latest.event_bytes + size <= self._batch_max_bytes
            and BatchConfiguration.model_validate(latest.configuration_json).same_generation(config)
        ):
            latest.event_count += 1
            latest.event_bytes += size
            latest.updated_at = now
            return latest
        batch = IngressBatchRecord(
            id=new_object_id("ibat"),
            organization_id=binding.organization_id,
            workspace_id=binding.workspace_id,
            binding_id=binding.id,
            sequence=binding.next_batch_sequence,
            configuration_json=config.model_dump(mode="json"),
            status="pending",
            event_count=1,
            event_bytes=size,
            append_until=max(now, assume_utc(binding.next_submission_at))
            + timedelta(milliseconds=config.input_batching.min_interval_ms),
            available_at=max(now, assume_utc(binding.next_submission_at)),
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
        binding.next_batch_sequence += 1
        binding.updated_at = now
        session.add(batch)
        await session.flush()
        return batch


async def _receipt(session: AsyncSession, event: IngressAdmissionRecord, *, duplicate: bool) -> DurableAdmissionReceipt:
    batch = await session.get(IngressBatchRecord, event.batch_id) if event.batch_id else None
    return DurableAdmissionReceipt.model_validate(
        {
            "admission_id": event.id,
            "status": batch.status if batch is not None else "rejected",
            "duplicate": duplicate,
            "reason_code": batch.rejection_reason if batch is not None else event.rejection_reason,
        }
    )
