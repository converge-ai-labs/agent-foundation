"""Durable, fenced A2A push notification delivery."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import anyio
import httpx2
from a2a.types import a2a_pb2 as a2a
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.durable_operations.outbox import OutboxClaim, complete_outbox, fail_outbox
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
from a13n_service.secrets import InternalSecretError, InternalSecretService
from a13n_service.secrets.domain import SecretOperation, SecretOwnerType, SecretUseContext
from a13n_service.storage import short_session, transaction

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


@dataclass(frozen=True, slots=True)
class A2APushFailure:
    error_code: str
    retryable: bool


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
                    A2APushConfigurationRecord.organization_id == event.tenant_id,
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
        secrets: InternalSecretService,
        *,
        poll_interval_seconds: float,
        lease_seconds: float,
        claim_limit: int,
        max_attempts: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
        delivery_timeout_seconds: float,
        max_response_bytes: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if poll_interval_seconds <= 0 or lease_seconds <= delivery_timeout_seconds:
            raise ValueError("A2A push polling and lease bounds are invalid")
        if claim_limit < 1 or claim_limit > 100 or max_attempts < 1:
            raise ValueError("A2A push claim and attempt bounds are invalid")
        if retry_base_seconds <= 0 or retry_max_seconds < retry_base_seconds:
            raise ValueError("A2A push retry backoff is invalid")
        if delivery_timeout_seconds <= 0 or max_response_bytes < 1:
            raise ValueError("A2A push delivery bounds are invalid")
        self._sessions = sessions
        self._http_client = http_client
        self._endpoint_policy = endpoint_policy
        self._secrets = secrets
        self._poll_interval_seconds = poll_interval_seconds
        self._lease_duration = timedelta(seconds=lease_seconds)
        self._claim_limit = claim_limit
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self._delivery_timeout_seconds = delivery_timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(self) -> None:
        while True:
            try:
                await self.publish_once()
            except anyio.get_cancelled_exc_class():
                raise
            except Exception:
                logger.exception("a2a_push_batch_failed", extra={"event": "a2a_push_batch_failed"})
            await anyio.sleep(self._poll_interval_seconds)

    async def publish_once(self) -> int:
        async with transaction(self._sessions) as database:
            claims = await claim_a2a_push_deliveries(
                database,
                now=self._now(),
                lease_duration=self._lease_duration,
                limit=self._claim_limit,
            )
        async with anyio.create_task_group() as deliveries:
            for claim in claims:
                deliveries.start_soon(self._publish_claim, claim)
        return len(claims)

    async def _publish_claim(self, claim: OutboxClaim) -> None:
        try:
            material = await self._load_material(claim)
        except A2APushMaterialError as error:
            await self._settle_failure(claim, A2APushFailure(error.error_code, error.retryable))
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
                or event.tenant_id != configuration.organization_id
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
            token_context = _secret_context(configuration, key=_TOKEN_KEY)
            credentials_context = _secret_context(configuration, key=_CREDENTIALS_KEY)
        try:
            token = None if token_context is None else await self._secrets.resolve(token_context)
            credentials = None if credentials_context is None else await self._secrets.resolve(credentials_context)
        except InternalSecretError as error:
            raise A2APushMaterialError("a2a_push_secret_unavailable", retryable=True) from error
        return A2APushMaterial(
            endpoint_url=configuration.endpoint_url,
            token=token,
            authentication_scheme=configuration.authentication_scheme,
            authentication_credentials=credentials,
            payload=payload,
        )

    async def _deliver(self, material: A2APushMaterial) -> A2APushFailure | None:
        try:
            with anyio.fail_after(self._delivery_timeout_seconds):
                endpoint_url = await self._endpoint_policy.validate(material.endpoint_url, resolve_dns=True)
                headers = {"Content-Type": "application/a2a+json", "A2A-Version": "1.0"}
                if material.token is not None:
                    headers["X-A2A-Notification-Token"] = material.token
                if material.authentication_scheme is not None:
                    credentials = material.authentication_credentials or ""
                    headers["Authorization"] = f"{material.authentication_scheme} {credentials}".rstrip()
                async with self._http_client.stream(
                    "POST",
                    endpoint_url,
                    headers=headers,
                    content=material.payload,
                    follow_redirects=False,
                ) as response:
                    if not await self._response_is_bounded(response):
                        return A2APushFailure("a2a_push_response_too_large", retryable=False)
                    if 200 <= response.status_code < 300:
                        return None
                    retryable = response.status_code in {408, 425, 429} or response.status_code >= 500
                    return A2APushFailure(f"a2a_push_http_{response.status_code}", retryable=retryable)
        except TimeoutError:
            return A2APushFailure("a2a_push_timed_out", retryable=True)
        except (EndpointPolicyError, httpx2.HTTPError):
            return A2APushFailure("a2a_push_transport_failed", retryable=True)

    async def _response_is_bounded(self, response: httpx2.Response) -> bool:
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self._max_response_bytes:
                    return False
            except ValueError:
                return False
        received = 0
        async for chunk in response.aiter_bytes():
            received += len(chunk)
            if received > self._max_response_bytes:
                return False
        return True

    async def _settle_failure(self, claim: OutboxClaim, failure: A2APushFailure) -> None:
        failed_at = self._now()
        retry_after = timedelta(seconds=self._retry_delay_seconds(claim.attempt_count))
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

    def _retry_delay_seconds(self, attempt_count: int) -> float:
        exponent = min(max(attempt_count - 1, 0), 16)
        return min(self._retry_max_seconds, self._retry_base_seconds * (2**exponent))

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
    state = {
        "run.running": a2a.TASK_STATE_WORKING,
        "run.waiting": a2a.TASK_STATE_INPUT_REQUIRED,
        "run.completed": a2a.TASK_STATE_COMPLETED,
        "run.failed": a2a.TASK_STATE_FAILED,
        "run.cancelled": a2a.TASK_STATE_CANCELED,
    }[event.event_type]
    status = a2a.TaskStatus(state=state)
    if event.event_type == "run.waiting":
        pending = event.payload.get("pending")
        if isinstance(pending, dict):
            value = Value()
            ParseDict(pending, value)
            status.message.CopyFrom(
                a2a.Message(
                    message_id=f"status-{event.id}",
                    role=a2a.ROLE_AGENT,
                    parts=[a2a.Part(data=value)],
                )
            )
    elif event.event_type == "run.failed":
        message = "The Agent task failed."
        failure = event.payload.get("failure")
        if isinstance(failure, dict):
            candidate = failure.get("message")
            if isinstance(candidate, str):
                message = candidate
        status.message.CopyFrom(
            a2a.Message(message_id=f"status-{event.id}", role=a2a.ROLE_AGENT, parts=[a2a.Part(text=message)])
        )
    return status


def _event_artifacts(task: A2ATaskBindingRecord, event: LifecycleEventRecord) -> list[a2a.Artifact]:
    if event.event_type != "run.completed":
        return []
    output_text = event.payload.get("output_text")
    output = event.payload.get("output")
    if isinstance(output_text, str):
        parts = [a2a.Part(text=output_text)]
    elif isinstance(output, str):
        parts = [a2a.Part(text=output)]
    elif output is not None:
        value = Value()
        ParseDict(cast(Any, output), value)
        parts = [a2a.Part(data=value)]
    else:
        return []
    return [a2a.Artifact(artifact_id=f"artifact-{task.id}-result", name="result", parts=parts)]


def _secret_context(
    configuration: A2APushConfigurationRecord,
    *,
    key: str,
) -> SecretUseContext | None:
    version = configuration.token_secret_version if key == _TOKEN_KEY else configuration.authentication_secret_version
    if version is None:
        return None
    return SecretUseContext(
        organization_id=configuration.organization_id,
        workspace_id=configuration.workspace_id,
        owner_type=SecretOwnerType.a2a_push_configuration,
        owner_id=configuration.id,
        key=key,
        operation=SecretOperation.callback,
        credential_generation=version,
    )


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
    "A2APushFailure",
    "A2APushMaterial",
    "A2APushMaterialError",
    "A2APushPublisher",
    "append_matching_a2a_push_outbox",
    "claim_a2a_push_deliveries",
]
