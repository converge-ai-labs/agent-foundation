"""Durable, fenced A2A push notification delivery."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin

import anyio
import httpx2
from a2a.types import a2a_pb2 as a2a
from google.protobuf.json_format import MessageToDict
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from a13n_service.background import PeriodicTask, Sweep
from a13n_service.credentials import CredentialSnapshot
from a13n_service.durable_operations.http_delivery import (
    DeliveryFailure,
    http_failure,
    response_is_bounded,
    retry_delay_seconds,
)
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.durable_operations.outbox import OutboxClaim, complete_outbox, fail_outbox
from a13n_service.durable_operations.publication import dispatch_outbox_batch
from a13n_service.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_service.iam import (
    AuthorizationError,
    PrincipalRef,
    PrincipalType,
    WorkspaceAction,
    authorize_persisted_agent_principal_actions,
)
from a13n_service.ids import new_object_id
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.secrets import SecretProtectionError, SecretProtector
from a13n_service.storage import short_session, transaction

from .a2a_projection import project_artifacts, project_status
from .models import A2APushConfigurationRecord, A2ATaskBindingRecord

logger = logging.getLogger("a13n_service.gateway.a2a_push")

_SIGNIFICANT_EVENT_TYPES = frozenset(
    {
        "run.running",
        "run.waiting",
        "run.completed",
        "run.failed",
        "run.cancelled",
    }
)
_TERMINAL_EVENT_TYPES = frozenset({"run.completed", "run.failed", "run.cancelled"})
_TOKEN_KEY = "token"
_CREDENTIALS_KEY = "authentication_credentials"


@dataclass(frozen=True, slots=True)
class A2APushMaterial:
    endpoint_url: str
    token: str | None
    authentication_scheme: str | None
    authentication_credentials: str | None
    payload: bytes


class A2APushMaterialError(RuntimeError):
    def __init__(self, error_code: str, *, retryable: bool = False) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.retryable = retryable


async def append_matching_a2a_push_outbox(
    database: AsyncSession,
    event: LifecycleEventRecord,
) -> tuple[OutboxRecord, ...]:
    """Append future-only delivery intents in the lifecycle fact transaction."""

    if event.event_type not in _SIGNIFICANT_EVENT_TYPES:
        return ()
    configurations = tuple(
        (
            await database.scalars(
                select(A2APushConfigurationRecord)
                .join(
                    A2ATaskBindingRecord,
                    and_(
                        A2ATaskBindingRecord.organization_id == A2APushConfigurationRecord.organization_id,
                        A2ATaskBindingRecord.id == A2APushConfigurationRecord.task_id,
                    ),
                )
                .where(
                    A2APushConfigurationRecord.organization_id == event.organization_id,
                    A2APushConfigurationRecord.state == "active",
                    A2ATaskBindingRecord.current_run_id == event.run_id,
                )
                .order_by(A2APushConfigurationRecord.id)
                .with_for_update(of=A2APushConfigurationRecord)
            )
        ).all()
    )
    records = tuple(
        OutboxRecord(
            id=new_object_id("dlv"),
            source_kind="lifecycle_event",
            source_id=event.id,
            destination_kind="a2a_push",
            destination_ref=_destination_ref(configuration.id, configuration.delivery_generation),
            status="pending",
            available_at=event.created_at,
            claim_generation=0,
            lease_expires_at=None,
            attempt_count=0,
            created_at=event.created_at,
            updated_at=event.created_at,
            published_at=None,
            dead_lettered_at=None,
            last_error_code=None,
        )
        for configuration in configurations
    )
    database.add_all(records)
    await database.flush()
    return records


async def claim_a2a_push_deliveries(
    database: AsyncSession,
    *,
    now: datetime,
    lease_duration: timedelta,
    limit: int,
) -> tuple[OutboxClaim, ...]:
    """Claim due rows without overtaking an earlier delivery for one configuration."""

    if lease_duration <= timedelta(0) or limit < 1 or limit > 100:
        raise ValueError("A2A push claim bounds are invalid")
    earlier = aliased(OutboxRecord)
    due = or_(
        and_(OutboxRecord.status == "pending", OutboxRecord.available_at <= now),
        and_(OutboxRecord.status == "publishing", OutboxRecord.lease_expires_at <= now),
    )
    no_earlier_unsettled = ~exists().where(
        earlier.destination_kind == "a2a_push",
        earlier.destination_ref == OutboxRecord.destination_ref,
        earlier.status.in_(("pending", "publishing")),
        or_(
            earlier.created_at < OutboxRecord.created_at,
            and_(earlier.created_at == OutboxRecord.created_at, earlier.id < OutboxRecord.id),
        ),
    )
    rows = (
        await database.scalars(
            select(OutboxRecord)
            .where(
                OutboxRecord.source_kind == "lifecycle_event",
                OutboxRecord.destination_kind == "a2a_push",
                due,
                no_earlier_unsettled,
            )
            .order_by(OutboxRecord.available_at, OutboxRecord.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    claims: list[OutboxClaim] = []
    for row in rows:
        row.status = "publishing"
        row.claim_generation += 1
        row.attempt_count += 1
        row.lease_expires_at = now + lease_duration
        row.updated_at = now
        row.published_at = None
        row.dead_lettered_at = None
        claims.append(
            OutboxClaim(
                outbox_id=row.id,
                source_kind=row.source_kind,
                source_id=row.source_id,
                destination_kind=row.destination_kind,
                destination_ref=row.destination_ref,
                generation=row.claim_generation,
                attempt_count=row.attempt_count,
            )
        )
    await database.flush()
    return tuple(claims)


class A2APushPublisher:
    """Deliver standard StreamResponse bodies through the shared durable outbox."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        http_client: httpx2.AsyncClient,
        endpoint_policy: EndpointPolicy,
        secret_protector: SecretProtector,
        *,
        poll_interval_seconds: float,
        lease_seconds: float,
        claim_limit: int,
        max_attempts: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
        delivery_timeout_seconds: float,
        max_response_bytes: int,
        max_redirects: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if poll_interval_seconds <= 0 or lease_seconds <= delivery_timeout_seconds:
            raise ValueError("A2A push polling and lease bounds are invalid")
        if claim_limit < 1 or claim_limit > 100 or max_attempts < 1:
            raise ValueError("A2A push claim and attempt bounds are invalid")
        if retry_base_seconds <= 0 or retry_max_seconds < retry_base_seconds:
            raise ValueError("A2A push retry backoff is invalid")
        if delivery_timeout_seconds <= 0 or max_response_bytes < 1 or max_redirects < 0:
            raise ValueError("A2A push delivery bounds are invalid")
        self._sessions = sessions
        self._http_client = http_client
        self._endpoint_policy = endpoint_policy
        self._secret_protector = secret_protector
        self._poll_interval_seconds = poll_interval_seconds
        self._lease_duration = timedelta(seconds=lease_seconds)
        self._claim_limit = claim_limit
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self._delivery_timeout_seconds = delivery_timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._max_redirects = max_redirects
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(self) -> None:
        await PeriodicTask(
            "a2a_push_publication",
            self.scan,
            interval_seconds=self._poll_interval_seconds,
            timeout_seconds=self._lease_duration.total_seconds() + self._delivery_timeout_seconds + 1,
        ).run()

    async def publish_once(self) -> int:
        return (await self.scan()).examined

    async def scan(self) -> Sweep:
        async with transaction(self._sessions) as database:
            claims = await claim_a2a_push_deliveries(
                database,
                now=self._now(),
                lease_duration=self._lease_duration,
                limit=self._claim_limit,
            )
        return await dispatch_outbox_batch(
            self._sessions,
            claims,
            self._publish_claim,
            timeout_seconds=self._lease_duration.total_seconds() + self._delivery_timeout_seconds,
            concurrency=self._claim_limit,
            clock=self._now,
        )

    async def _publish_claim(self, claim: OutboxClaim) -> None:
        try:
            material = await self._load_material(claim)
        except A2APushMaterialError as error:
            await self._settle_failure(claim, DeliveryFailure(error.error_code, error.retryable))
            return
        if material is None:
            return
        failure = await self._deliver(material)
        if failure is None:
            async with transaction(self._sessions) as database:
                await complete_outbox(database, claim, completed_at=self._now())
            return
        await self._settle_failure(claim, failure)

    async def _load_material(self, claim: OutboxClaim) -> A2APushMaterial | None:
        config_id, generation = _parse_destination_ref(claim.destination_ref)
        async with short_session(self._sessions) as database:
            outbox = await database.scalar(
                select(OutboxRecord).where(
                    OutboxRecord.id == claim.outbox_id,
                    OutboxRecord.source_kind == claim.source_kind,
                    OutboxRecord.source_id == claim.source_id,
                    OutboxRecord.destination_kind == claim.destination_kind,
                    OutboxRecord.destination_ref == claim.destination_ref,
                    OutboxRecord.status == "publishing",
                    OutboxRecord.claim_generation == claim.generation,
                    OutboxRecord.lease_expires_at > self._now(),
                )
            )
            if outbox is None:
                return None
            configuration = await database.get(A2APushConfigurationRecord, config_id)
            event = await database.scalar(
                select(LifecycleEventRecord).where(LifecycleEventRecord.id == claim.source_id)
            )
            if configuration is None or event is None:
                raise A2APushMaterialError("a2a_push_binding_missing")
            if configuration.state != "active" or configuration.delivery_generation != generation:
                raise A2APushMaterialError("a2a_push_configuration_fenced")
            task = await database.get(A2ATaskBindingRecord, configuration.task_id)
            if (
                task is None
                or task.organization_id != configuration.organization_id
                or event.organization_id != configuration.organization_id
                or event.run_id not in task.run_ids_json
                or event.event_type not in _SIGNIFICANT_EVENT_TYPES
            ):
                raise A2APushMaterialError("a2a_push_authority_mismatch")
            principal = PrincipalRef(
                principal_type=PrincipalType(configuration.creator_principal_type),
                principal_id=configuration.creator_principal_id,
            )
            try:
                await authorize_persisted_agent_principal_actions(
                    database,
                    principal=principal,
                    organization_id=configuration.organization_id,
                    workspace_id=configuration.workspace_id,
                    agent_id=task.agent_id,
                    actions=frozenset(
                        {
                            WorkspaceAction.run_read,
                            WorkspaceAction.a2a_push_configuration_manage,
                        }
                    ),
                )
            except AuthorizationError as error:
                raise A2APushMaterialError("a2a_push_authority_revoked") from error
            payload = _event_payload(task, event)
            credential_snapshot = configuration.credential_snapshot()
        try:
            token, credentials = _decrypt_credential_bundle(credential_snapshot, self._secret_protector)
        except (SecretProtectionError, ValueError) as error:
            raise A2APushMaterialError("a2a_push_secret_unavailable", retryable=True) from error
        return A2APushMaterial(
            endpoint_url=configuration.endpoint_url,
            token=token,
            authentication_scheme=configuration.authentication_scheme,
            authentication_credentials=credentials,
            payload=payload,
        )

    async def _deliver(self, material: A2APushMaterial) -> DeliveryFailure | None:
        try:
            with anyio.fail_after(self._delivery_timeout_seconds):
                current = await self._endpoint_policy.validate(material.endpoint_url, resolve_dns=True)
                headers = {"Content-Type": "application/a2a+json", "A2A-Version": "1.0"}
                if material.token is not None:
                    headers["X-A2A-Notification-Token"] = material.token
                if material.authentication_scheme is not None:
                    credentials = material.authentication_credentials or ""
                    headers["Authorization"] = f"{material.authentication_scheme} {credentials}".rstrip()
                for redirect_count in range(self._max_redirects + 1):
                    async with self._http_client.stream(
                        "POST",
                        current,
                        headers=headers,
                        content=material.payload,
                        follow_redirects=False,
                    ) as response:
                        if not await response_is_bounded(response, maximum_bytes=self._max_response_bytes):
                            return DeliveryFailure("a2a_push_response_too_large", retryable=False)
                        if response.status_code not in {301, 302, 303, 307, 308}:
                            return http_failure(response.status_code, error_prefix="a2a_push")
                        if redirect_count == self._max_redirects:
                            return DeliveryFailure("a2a_push_redirect_limit", retryable=False)
                        location = response.headers.get("location")
                        if location is None:
                            return DeliveryFailure("a2a_push_redirect_invalid", retryable=False)
                        try:
                            current, same_origin = await self._endpoint_policy.validate_redirect(
                                current,
                                urljoin(current, location),
                                resolve_dns=True,
                            )
                        except EndpointPolicyError:
                            return DeliveryFailure("a2a_push_redirect_unsafe", retryable=False)
                        if not same_origin:
                            headers.pop("Authorization", None)
                            headers.pop("X-A2A-Notification-Token", None)
                return DeliveryFailure("a2a_push_redirect_limit", retryable=False)
        except TimeoutError:
            return DeliveryFailure("a2a_push_timed_out", retryable=True)
        except (EndpointPolicyError, httpx2.HTTPError):
            return DeliveryFailure("a2a_push_transport_failed", retryable=True)

    async def _settle_failure(self, claim: OutboxClaim, failure: DeliveryFailure) -> None:
        failed_at = self._now()
        retry_after = timedelta(
            seconds=retry_delay_seconds(
                claim.attempt_count, base=self._retry_base_seconds, maximum=self._retry_max_seconds
            )
        )
        async with transaction(self._sessions) as database:
            settled = await fail_outbox(
                database,
                claim,
                failed_at=failed_at,
                error_code=failure.error_code,
                retryable=failure.retryable,
                retry_after=retry_after,
                max_attempts=self._max_attempts,
            )
        if settled:
            logger.warning(
                "a2a_push_delivery_failed",
                extra={
                    "event": "a2a_push_delivery_failed",
                    "delivery_id": claim.outbox_id,
                    "error_code": failure.error_code,
                    "retryable": failure.retryable and claim.attempt_count < self._max_attempts,
                },
            )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("A2A push publisher clock must include a UTC offset")
        return value.astimezone(UTC)


def _event_payload(task: A2ATaskBindingRecord, event: LifecycleEventRecord) -> bytes:
    status = _event_status(event)
    if event.event_type in _TERMINAL_EVENT_TYPES:
        stream_response = a2a.StreamResponse(
            task=a2a.Task(
                id=task.id,
                context_id=task.context_id,
                status=status,
                artifacts=_event_artifacts(task, event),
            )
        )
    else:
        stream_response = a2a.StreamResponse(
            status_update=a2a.TaskStatusUpdateEvent(
                task_id=task.id,
                context_id=task.context_id,
                status=status,
            )
        )
    payload = MessageToDict(stream_response, preserving_proto_field_name=False)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _event_status(event: LifecycleEventRecord) -> a2a.TaskStatus:
    return project_status(
        run_status=event.event_type.removeprefix("run."),
        wait_reason=event.payload.get("wait_reason"),
        pending=event.payload.get("pending"),
        failure=event.payload.get("failure"),
        message_id=f"status-{event.id}",
    )


def _event_artifacts(task: A2ATaskBindingRecord, event: LifecycleEventRecord) -> list[a2a.Artifact]:
    if event.event_type != "run.completed":
        return []
    output_text = event.payload.get("output_text")
    return project_artifacts(
        task_id=task.id,
        output_text=output_text if isinstance(output_text, str) else None,
        output=event.payload.get("output"),
    )


def _decrypt_credential_bundle(
    snapshot: CredentialSnapshot,
    protector: SecretProtector,
) -> tuple[str | None, str | None]:
    if snapshot.ciphertext is None:
        return None, None
    value = json.loads(snapshot.decrypt(protector))
    if not isinstance(value, dict) or set(value).difference({_TOKEN_KEY, _CREDENTIALS_KEY}):
        raise ValueError("A2A push credential bundle is invalid")
    token = value.get(_TOKEN_KEY)
    credentials = value.get(_CREDENTIALS_KEY)
    if (token is not None and not isinstance(token, str)) or (
        credentials is not None and not isinstance(credentials, str)
    ):
        raise ValueError("A2A push credential bundle is invalid")
    return token, credentials


def _destination_ref(config_id: str, generation: int) -> str:
    return f"{config_id}:{generation}"


def _parse_destination_ref(value: str) -> tuple[str, int]:
    config_id, separator, generation_text = value.rpartition(":")
    if not separator:
        raise A2APushMaterialError("a2a_push_destination_invalid")
    try:
        generation = int(generation_text)
    except ValueError as error:
        raise A2APushMaterialError("a2a_push_destination_invalid") from error
    if not config_id or generation < 1:
        raise A2APushMaterialError("a2a_push_destination_invalid")
    return config_id, generation


__all__ = [
    "A2APushMaterial",
    "A2APushMaterialError",
    "A2APushPublisher",
    "append_matching_a2a_push_outbox",
    "claim_a2a_push_deliveries",
]
