"""Fenced persistence operations for lifecycle Webhook deliveries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.durable_operations.outbox import OutboxClaim, claim_outbox
from a13n_service.interactions.models import SessionRecord
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.secrets.models import SecretRecord

from .delivery import DeliveryEnvelope
from .models import HookSubscriptionRevisionRecord


@dataclass(frozen=True, slots=True)
class EncryptedSigningSecret:
    secret_id: str
    organization_id: str
    workspace_id: str
    owner_type: str
    owner_id: str
    key: str
    version: int
    ciphertext: bytes
    nonce: bytes
    encryption_key_id: str


@dataclass(frozen=True, slots=True)
class WebhookDeliveryMaterial:
    endpoint_url: str
    signature_profile: str
    envelope: DeliveryEnvelope
    signing_secret: EncryptedSigningSecret


class WebhookMaterialError(RuntimeError):
    """A claimed row cannot be safely reconstructed from its authorities."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


async def claim_webhook_deliveries(
    database: AsyncSession,
    *,
    now: datetime,
    lease_duration: timedelta,
    limit: int,
) -> tuple[OutboxClaim, ...]:
    if lease_duration <= timedelta(0):
        raise ValueError("Webhook claim lease must be positive")
    if limit < 1 or limit > 100:
        raise ValueError("Webhook claim limit must be between 1 and 100")
    return await claim_outbox(
        database,
        source_kind="lifecycle_event",
        destination_kind="webhook",
        now=now,
        lease_duration=lease_duration,
        limit=limit,
    )


async def load_webhook_delivery(
    database: AsyncSession,
    claim: OutboxClaim,
    *,
    now: datetime,
) -> WebhookDeliveryMaterial | None:
    outbox = await database.scalar(
        select(OutboxRecord).where(
            OutboxRecord.id == claim.outbox_id,
            OutboxRecord.source_kind == claim.source_kind,
            OutboxRecord.source_id == claim.source_id,
            OutboxRecord.destination_kind == claim.destination_kind,
            OutboxRecord.destination_ref == claim.destination_ref,
            OutboxRecord.status == "publishing",
            OutboxRecord.claim_generation == claim.generation,
            OutboxRecord.lease_expires_at > now,
        )
    )
    if outbox is None:
        return None
    event = await database.scalar(select(LifecycleEventRecord).where(LifecycleEventRecord.id == outbox.source_id))
    if event is None:
        raise WebhookMaterialError("webhook_source_missing")
    revision = await database.get(HookSubscriptionRevisionRecord, outbox.destination_ref)
    if revision is None:
        raise WebhookMaterialError("webhook_destination_missing")
    workspace_id = await database.scalar(
        select(SessionRecord.workspace_id).where(
            SessionRecord.tenant_id == event.tenant_id,
            SessionRecord.id == event.session_id,
        )
    )
    if (
        event.tenant_id != revision.organization_id
        or workspace_id != revision.workspace_id
        or event.event_type not in revision.hook_names
    ):
        raise WebhookMaterialError("webhook_destination_authority_mismatch")
    secret = await database.get(SecretRecord, revision.signing_secret_id)
    if (
        secret is None
        or secret.organization_id != revision.organization_id
        or secret.workspace_id != revision.workspace_id
        or secret.owner_type != "workspace"
        or secret.owner_id != revision.workspace_id
        or secret.deleted_at is not None
        or secret.ciphertext is None
        or secret.nonce is None
        or secret.encryption_key_id is None
    ):
        raise WebhookMaterialError("webhook_signing_secret_unavailable")
    harness_run_id = None
    if event.event_type == "run_attempt.running":
        candidate = event.payload.get("harness_run_id")
        if not isinstance(candidate, str) or not candidate:
            raise WebhookMaterialError("webhook_source_invalid")
        harness_run_id = candidate
    try:
        envelope = DeliveryEnvelope(
            delivery_id=outbox.id,
            hook_subscription_id=revision.hook_subscription_id,
            hook_name=event.event_type,
            hook_schema_version=event.schema_version,
            source_id=event.id,
            workspace_id=revision.workspace_id,
            resource_type=_resource_type(event.entity_type),
            resource_id=event.entity_id,
            resource_seq=event.resource_seq,
            resource_version=event.entity_version,
            session_id=event.session_id,
            thread_id=event.thread_id,
            run_id=event.run_id,
            run_attempt_id=event.run_attempt_id,
            harness_run_id=harness_run_id,
            occurred_at=_utc(event.occurred_at),
            payload=event.payload,
        )
    except ValidationError as error:
        raise WebhookMaterialError("webhook_source_invalid") from error
    return WebhookDeliveryMaterial(
        endpoint_url=revision.endpoint_url,
        signature_profile=revision.signature_profile,
        envelope=envelope,
        signing_secret=EncryptedSigningSecret(
            secret_id=secret.id,
            organization_id=secret.organization_id,
            workspace_id=secret.workspace_id,
            owner_type=secret.owner_type,
            owner_id=secret.owner_id,
            key=secret.key,
            version=secret.version,
            ciphertext=bytes(secret.ciphertext),
            nonce=bytes(secret.nonce),
            encryption_key_id=secret.encryption_key_id,
        ),
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _resource_type(value: str) -> Literal["run", "run_attempt"]:
    if value == "run":
        return "run"
    if value == "run_attempt":
        return "run_attempt"
    raise WebhookMaterialError("webhook_source_invalid")


__all__ = [
    "EncryptedSigningSecret",
    "WebhookDeliveryMaterial",
    "WebhookMaterialError",
    "claim_webhook_deliveries",
    "load_webhook_delivery",
]
