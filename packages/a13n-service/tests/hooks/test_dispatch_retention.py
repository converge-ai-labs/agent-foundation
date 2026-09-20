"""Pending fan-out pins completed inline owners, then hands retention to Outbox."""

from datetime import timedelta

import pytest
from a13n_harness import SafeFailure
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.hooks import InlineHookSubscriptionInput, WebhookDestinationConfig
from a13n_service.hooks.dispatch import retry_failed_hook_dispatch
from a13n_service.hooks.dispatcher import HookDispatcher
from a13n_service.hooks.models import HookSubscriptionRecord, HookSubscriptionRevisionRecord
from a13n_service.hooks.retention import HookRetention
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.lifecycle.retention import LifecycleRetentionReconciler
from a13n_service.storage import short_session, transaction
from a13n_service.storage.object_store import LocalObjectStore
from sqlalchemy import select

from tests.gateway.test_commands import _actor, _commands, _complete_run, _Freezing, _frozen, _Preparation, _request
from tests.hooks.support import SECRET_ID, seed_hook_actor_access, seed_run_and_secret
from tests.interactions.conftest import NOW, ORGANIZATION_ID, WORKSPACE_ID

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("dispatch_state", ["pending", "failed"])
async def test_inline_and_fact_retention_wait_for_dispatch_then_delivery(
    hook_interaction_sessions, tmp_path, monkeypatch, dispatch_state
):
    sessions = hook_interaction_sessions
    await seed_run_and_secret(sessions)
    await seed_hook_actor_access(sessions)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(sessions, objects, _Preparation(), _Freezing([_frozen()]))
    receipt = await commands.runs.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="dispatch-retention",
        request=_request().model_copy(
            update={
                "hook_subscription": InlineHookSubscriptionInput(
                    hook_names=("run.accepted", "run.completed"),
                    webhook=WebhookDestinationConfig(
                        endpoint_url="https://93.184.216.34/retention",
                        signing_secret_id=SECRET_ID,
                    ),
                )
            }
        ),
    )
    await _complete_run(sessions, objects, run_id=receipt.run_id)
    now = NOW + timedelta(days=3)
    monkeypatch.setattr("a13n_service.hooks.retention.utc_now", lambda: now)
    hooks = HookRetention(sessions, minimum_age=timedelta(days=1), batch_limit=10)
    lifecycle = LifecycleRetentionReconciler(
        sessions,
        event_horizon=timedelta(days=1),
        published_delivery_horizon=timedelta(days=1),
        dead_letter_horizon=timedelta(days=1),
        poll_interval_seconds=60,
        batch_limit=100,
        clock=lambda: now,
    )
    async with transaction(sessions) as database:
        facts = tuple(await database.scalars(select(LifecycleEventRecord)))
        event_ids = [event.id for event in facts]
        for event in facts:
            event.projection_state = "projected"
            event.projection_next_attempt_at = None
            event.projected_at = NOW
            if dispatch_state == "failed":
                event.hook_dispatch_state = "failed"
                event.hook_dispatch_attempts = 10
                event.hook_dispatch_next_attempt_at = None
                event.hook_dispatch_error_json = SafeFailure(code="test_failure", message="Test").model_dump(
                    mode="json"
                )

    assert (await lifecycle.reconcile_once()).lifecycle_events_deleted == 0
    assert (await hooks.scan()).completed == 0
    async with short_session(sessions) as database:
        head = await database.get(HookSubscriptionRecord, receipt.hook_subscription_id)
        assert head.expired_at is not None
        revision_id = head.current_revision_id
        assert await database.get(HookSubscriptionRevisionRecord, revision_id) is not None

    if dispatch_state == "failed":
        async with transaction(sessions) as database:
            for event_id in event_ids:
                assert await retry_failed_hook_dispatch(
                    database, organization_id=ORGANIZATION_ID, event_id=event_id, now=now
                )
    assert (await HookDispatcher(sessions, clock=lambda: now).scan()).completed == len(event_ids)
    assert (await lifecycle.reconcile_once()).lifecycle_events_deleted == 0
    assert (await hooks.scan()).completed == 0

    async with transaction(sessions) as database:
        deliveries = tuple(await database.scalars(select(OutboxRecord)))
        assert len(deliveries) == 2
        assert {delivery.destination_ref for delivery in deliveries} == {revision_id}
        for delivery in deliveries:
            delivery.status = "published"
            delivery.published_at = now
    now += timedelta(days=2)
    assert (await lifecycle.reconcile_once()).lifecycle_events_deleted == len(event_ids)
    # The bounded Hook sweep wraps its local scan cursor between passes.
    for _ in range(2):
        await hooks.scan()
    async with short_session(sessions) as database:
        assert await database.get(HookSubscriptionRecord, receipt.hook_subscription_id) is None
        assert await database.get(HookSubscriptionRevisionRecord, revision_id) is None
