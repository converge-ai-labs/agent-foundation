"""Subscription management can commit after matching without changing that delivery."""

from datetime import timedelta

import pytest
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.hooks.dispatch import claim_hook_events, dispatch_hook_event
from a13n_service.hooks.dispatcher import HookDispatcher
from a13n_service.hooks.models import HookSubscriptionRecord, HookSubscriptionRevisionRecord
from a13n_service.hooks.persistence import create_hook_subscription, lock_hook_workspace
from a13n_service.hooks.retention import HookRetention
from a13n_service.interactions.lifecycle import LifecycleWriter
from a13n_service.interactions.models import RunRecord
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.storage import short_session, transaction
from anyio import Event, create_task_group, fail_after
from sqlalchemy import select

from tests.hooks.support import RUN_ID, SECRET_ID, seed_run_and_secret
from tests.hooks.test_persistence import _input
from tests.interactions.conftest import NOW, ORGANIZATION_ID, SESSION_ID, THREAD_ID, USER_ID, WORKSPACE_ID

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("change", ["disable", "replace", "create"])
async def test_matching_observation_does_not_block_management_or_change_before_commit(
    hook_interaction_sessions, monkeypatch, change
):
    sessions = hook_interaction_sessions
    await seed_run_and_secret(sessions)

    async def create(database):
        return await create_hook_subscription(
            database,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            actor_type="user",
            actor_id=USER_ID,
            subscription=_input("run.accepted")
            .bind_run_scope(session_id=SESSION_ID, thread_id=THREAD_ID, run_id=RUN_ID)
            .model_copy(update={"session_id": None, "thread_id": None, "run_id": None}),
            now=NOW,
        )

    original = None
    if change != "create":
        async with transaction(sessions) as database:
            original = await create(database)
    matched, changed = Event(), Event()
    events = []
    replacement_id = "hsubr_8282828282828282"

    async with transaction(sessions) as database:
        run = await database.get(RunRecord, RUN_ID, with_for_update=True)
        events.append(
            await LifecycleWriter().append_run_lifecycle(
                database, run, "run.accepted", occurred_at=NOW, actor_type="system", actor_id=None
            )
        )

    async def dispatch():
        async with transaction(sessions) as database:
            (event,) = await claim_hook_events(database, now=NOW, limit=1)
            await dispatch_hook_event(database, event, now=NOW)
            matched.set()
            # Test-only barrier exposes matching/management/commit overlap.
            await changed.wait()

    async def manage():
        await matched.wait()
        async with transaction(sessions) as database:
            await lock_hook_workspace(database, organization_id=ORGANIZATION_ID, workspace_id=WORKSPACE_ID)
            if change == "create":
                await create(database)
            else:
                head = await database.get(HookSubscriptionRecord, original.id, with_for_update=True)
                if change == "disable":
                    head.enabled = False
                else:
                    database.add(
                        HookSubscriptionRevisionRecord(
                            id=replacement_id,
                            organization_id=ORGANIZATION_ID,
                            workspace_id=WORKSPACE_ID,
                            hook_subscription_id=head.id,
                            version=2,
                            hook_names=["run.accepted"],
                            session_id=None,
                            thread_id=None,
                            run_id=None,
                            endpoint_url="https://new.example.com/hook",
                            signing_secret_id=SECRET_ID,
                            signature_profile="hmac_sha256_v1",
                            created_by_type="user",
                            created_by_id=USER_ID,
                            created_at=NOW,
                        )
                    )
                    head.version = 2
                    head.current_revision_id = replacement_id
                head.touch(NOW)
        if change == "replace":
            # The immutable old Revision is pinned while the selecting Outbox is
            # uncommitted, and by that Outbox after commit. Configuration heads
            # remain independently mutable during the pin.
            monkeypatch.setattr("a13n_service.hooks.retention.utc_now", lambda: NOW + timedelta(days=1))
            await HookRetention(sessions, minimum_age=timedelta(seconds=1), batch_limit=10).scan()
            async with short_session(sessions) as database:
                assert await database.get(HookSubscriptionRevisionRecord, original.current_revision_id) is not None
        changed.set()

    with fail_after(10):
        async with create_task_group() as tasks:
            tasks.start_soon(dispatch)
            tasks.start_soon(manage)

    async with transaction(sessions) as database:
        run = await database.get(RunRecord, RUN_ID, with_for_update=True)
        events.append(
            await LifecycleWriter().append_run_lifecycle(
                database, run, "run.accepted", occurred_at=NOW, actor_type="system", actor_id=None
            )
        )
    await HookDispatcher(sessions, clock=lambda: NOW).scan()
    async with short_session(sessions) as database:
        deliveries = (await database.scalars(select(OutboxRecord))).all()
        first = [row.destination_ref for row in deliveries if row.source_id == events[0]]
        second = [row.destination_ref for row in deliveries if row.source_id == events[1]]
        assert first == ([] if change == "create" else [original.current_revision_id])
        if change == "replace":
            assert second == [replacement_id]
        else:
            assert len(second) == (1 if change == "create" else 0)
        facts = (await database.scalars(select(LifecycleEventRecord).order_by(LifecycleEventRecord.resource_seq))).all()
        assert [fact.resource_seq for fact in facts] == [1, 2]
