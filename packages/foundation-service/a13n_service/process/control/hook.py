"""Hook delivery control-plane construction."""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta

import httpx2

from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.hooks.management import HookSubscriptionService
from a13n_service.hooks.publisher import WebhookPublisher
from a13n_service.lifecycle.retention import LifecycleRetentionReconciler
from a13n_service.lifecycle.service import LifecycleEventService
from a13n_service.process.background import BackgroundTask
from a13n_service.process.runtime import SharedRuntime
from a13n_service.settings import Settings


@dataclass(frozen=True, slots=True)
class _HookBundle:
    subscriptions: HookSubscriptionService
    lifecycle_events: LifecycleEventService
    delivery_task: BackgroundTask
    retention_task: BackgroundTask


async def build_hook_bundle(
    settings: Settings,
    shared: SharedRuntime,
    stack: AsyncExitStack,
) -> _HookBundle:
    """Construct Hook APIs and the bounded publisher owned by Control roles."""

    endpoint_policy = EndpointPolicy.from_operator_allowlist(
        private_domains=settings.webhook_private_endpoint_domains,
        private_cidrs=settings.webhook_private_endpoint_cidrs,
    )

    async def validate_request(request: httpx2.Request) -> None:
        await endpoint_policy.validate(str(request.url), resolve_dns=True)

    subscriptions = HookSubscriptionService(
        shared.storage.sessions,
        endpoint_policy,
        validation_timeout_seconds=settings.webhook_request_timeout_seconds,
    )
    http_client = await stack.enter_async_context(
        httpx2.AsyncClient(
            follow_redirects=False,
            timeout=settings.webhook_request_timeout_seconds,
            event_hooks={"request": [validate_request]},
        )
    )
    publisher = WebhookPublisher(
        shared.storage.sessions,
        http_client,
        endpoint_policy,
        shared.secret_protector,
        poll_interval_seconds=settings.webhook_poll_interval_seconds,
        lease_seconds=settings.webhook_claim_lease_seconds,
        claim_limit=settings.webhook_claim_limit,
        max_attempts=settings.webhook_max_attempts,
        retry_base_seconds=settings.webhook_retry_base_seconds,
        retry_max_seconds=settings.webhook_retry_max_seconds,
        delivery_timeout_seconds=settings.webhook_request_timeout_seconds,
        max_response_bytes=settings.webhook_max_response_bytes,
    )
    retention = LifecycleRetentionReconciler(
        shared.storage.sessions,
        event_horizon=timedelta(days=settings.lifecycle_retention_days),
        published_delivery_horizon=timedelta(days=settings.lifecycle_published_delivery_retention_days),
        dead_letter_horizon=timedelta(days=settings.lifecycle_dead_letter_retention_days),
        poll_interval_seconds=settings.lifecycle_retention_poll_interval_seconds,
        batch_limit=settings.lifecycle_retention_batch_limit,
    )
    return _HookBundle(
        subscriptions=subscriptions,
        lifecycle_events=LifecycleEventService(shared.storage.sessions),
        delivery_task=BackgroundTask("webhook publisher", publisher.run),
        retention_task=BackgroundTask("lifecycle retention reconciler", retention.run),
    )


__all__ = ["build_hook_bundle"]
