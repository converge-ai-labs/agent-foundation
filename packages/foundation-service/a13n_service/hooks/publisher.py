"""Bounded, fenced Webhook publisher for lifecycle Hook Outbox rows."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import anyio
import httpx2
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.durable_operations.outbox import OutboxClaim, complete_outbox, fail_outbox
from a13n_service.endpoint_policy import EndpointPolicyError
from a13n_service.secrets import SecretProtectionError, SecretProtector
from a13n_service.storage import short_session, transaction

from .delivery import signed_request_headers
from .outbox import (
    WebhookDeliveryMaterial,
    WebhookMaterialError,
    claim_webhook_deliveries,
    load_webhook_delivery,
)
from .validation import EndpointValidator

logger = logging.getLogger("a13n_service.hooks.publisher")


@dataclass(frozen=True, slots=True)
class DeliveryFailure:
    error_code: str
    retryable: bool


class WebhookPublisher:
    """Claim durable intents, deliver outside SQL, then settle under a fence."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        http_client: httpx2.AsyncClient,
        endpoint_validator: EndpointValidator,
        secret_protector: SecretProtector,
        *,
        poll_interval_seconds: float = 1,
        lease_seconds: float = 30,
        claim_limit: int = 25,
        max_attempts: int = 10,
        retry_base_seconds: float = 2,
        retry_max_seconds: float = 300,
        delivery_timeout_seconds: float = 10,
        max_response_bytes: int = 64 * 1024,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if poll_interval_seconds <= 0 or lease_seconds <= 0:
            raise ValueError("Webhook polling and lease durations must be positive")
        if claim_limit < 1 or claim_limit > 100 or max_attempts < 1:
            raise ValueError("Webhook claim and attempt bounds are invalid")
        if retry_base_seconds <= 0 or retry_max_seconds < retry_base_seconds:
            raise ValueError("Webhook retry backoff is invalid")
        if delivery_timeout_seconds <= 0 or max_response_bytes < 1:
            raise ValueError("Webhook delivery timeout and response limit must be positive")
        self._sessions = sessions
        self._http_client = http_client
        self._endpoint_validator = endpoint_validator
        self._secret_protector = secret_protector
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
                logger.exception("webhook_publish_batch_failed", extra={"event": "webhook_publish_batch_failed"})
            await anyio.sleep(self._poll_interval_seconds)

    async def publish_once(self) -> int:
        async with transaction(self._sessions) as database:
            claims = await claim_webhook_deliveries(
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
        except WebhookMaterialError as error:
            await self._settle_failure(claim, DeliveryFailure(error.error_code, retryable=False))
            return
        if material is None:
            return
        failure = await self._deliver(material)
        if failure is None:
            async with transaction(self._sessions) as database:
                settled = await complete_outbox(database, claim, completed_at=self._now())
            if settled:
                logger.info(
                    "webhook_delivery_published",
                    extra={"event": "webhook_delivery_published", "delivery_id": claim.outbox_id},
                )
            return
        await self._settle_failure(claim, failure)

    async def _load_material(self, claim: OutboxClaim) -> WebhookDeliveryMaterial | None:
        async with short_session(self._sessions) as database:
            return await load_webhook_delivery(database, claim, now=self._now())

    async def _deliver(self, material: WebhookDeliveryMaterial) -> DeliveryFailure | None:
        try:
            with anyio.fail_after(self._delivery_timeout_seconds):
                return await self._deliver_before_deadline(material)
        except TimeoutError:
            return DeliveryFailure("webhook_delivery_timed_out", retryable=True)

    async def _deliver_before_deadline(self, material: WebhookDeliveryMaterial) -> DeliveryFailure | None:
        if material.signature_profile != "hmac_sha256_v1":
            return DeliveryFailure("webhook_signature_profile_unsupported", retryable=False)
        try:
            endpoint_url = await self._endpoint_validator.validate(material.endpoint_url, resolve_dns=True)
        except EndpointPolicyError:
            return DeliveryFailure("webhook_endpoint_rejected", retryable=True)
        try:
            signing_secret = self._decrypt(material)
        except SecretProtectionError:
            return DeliveryFailure("webhook_signing_secret_unavailable", retryable=True)

        signed_at = self._now()
        body = material.envelope.canonical_bytes()
        headers = signed_request_headers(material.envelope, signing_secret=signing_secret, signed_at=signed_at)
        try:
            async with self._http_client.stream(
                "POST",
                endpoint_url,
                headers=headers,
                content=body,
                follow_redirects=False,
            ) as response:
                if not await self._response_is_bounded(response):
                    return DeliveryFailure("webhook_response_too_large", retryable=False)
                if 200 <= response.status_code < 300:
                    return None
                retryable = response.status_code in {408, 425, 429} or response.status_code >= 500
                return DeliveryFailure(f"webhook_http_{response.status_code}", retryable=retryable)
        except (EndpointPolicyError, httpx2.HTTPError):
            return DeliveryFailure("webhook_transport_failed", retryable=True)

    def _decrypt(self, material: WebhookDeliveryMaterial) -> str:
        secret = material.signing_secret
        return self._secret_protector.decrypt(
            ciphertext=secret.ciphertext,
            nonce=secret.nonce,
            encryption_key_id=secret.encryption_key_id,
            secret_id=secret.secret_id,
            organization_id=secret.organization_id,
            workspace_id=secret.workspace_id,
            owner_type=secret.owner_type,
            owner_id=secret.owner_id,
            key=secret.key,
            version=secret.version,
        )

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

    async def _settle_failure(self, claim: OutboxClaim, failure: DeliveryFailure) -> None:
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
                "webhook_delivery_failed",
                extra={
                    "event": "webhook_delivery_failed",
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
            raise ValueError("Webhook publisher clock must include a UTC offset")
        return value.astimezone(UTC)


__all__ = ["DeliveryFailure", "EndpointValidator", "WebhookPublisher"]
