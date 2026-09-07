from datetime import timedelta

import anyio
import pytest
from a13n_service.hooks.domain import CreateHookSubscriptionRequest, WebhookDestinationConfig
from a13n_service.hooks.models import HookSubscriptionRecord, HookSubscriptionRevisionRecord
from a13n_service.hooks.persistence import create_hook_subscription
from a13n_service.hooks.retention import HookRetention
from a13n_service.iam.audit import SystemAuditActor, security_audit_record
from a13n_service.storage import short_session, transaction
from sqlalchemy import select

from tests.hooks.support import RUN_ID, SECRET_ID, seed_run_and_secret
from tests.interactions.conftest import NOW, ORGANIZATION_ID, USER_ID, WORKSPACE_ID

pytestmark = pytest.mark.anyio


async def _head(sessions, *, inline=False):
    async with transaction(sessions) as database:
        head = await create_hook_subscription(
            database,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            actor_type="user",
            actor_id=USER_ID,
            now=NOW,
            subscription=CreateHookSubscriptionRequest(
                hook_names=("run.completed",),
                run_id=RUN_ID,
                webhook=WebhookDestinationConfig(endpoint_url="https://example.com/hook", signing_secret_id=SECRET_ID),
            ),
            inline_run_id=RUN_ID if inline else None,
        )
        head.deleted_at = NOW
        return head.id, head.current_revision_id


@pytest.fixture(params=("hook_interaction_sessions", "hook_postgres_sessions"))
def retention_sessions(request):
    return request.getfixturevalue(request.param)


async def test_collect_head_revision_cycle_with_overlapping_replicas(retention_sessions):
    sessions = retention_sessions
    await seed_run_and_secret(sessions)
    head_id, revision_id = await _head(sessions)
    results = []

    async def scan():
        results.append(await HookRetention(sessions, minimum_age=timedelta(days=1), batch_limit=1).scan())

    async with anyio.create_task_group() as group:
        group.start_soon(scan)
        group.start_soon(scan)
    assert sum(result.completed for result in results) == 2
    async with short_session(sessions) as database:
        assert await database.get(HookSubscriptionRecord, head_id) is None
        assert await database.get(HookSubscriptionRevisionRecord, revision_id) is None


async def test_audit_pin_does_not_starve_another_head(hook_interaction_sessions):
    sessions = hook_interaction_sessions
    await seed_run_and_secret(sessions)
    pinned, _ = await _head(sessions)
    collectible, _ = await _head(sessions)
    async with transaction(sessions) as database:
        database.add(
            security_audit_record(
                audit_id="aud_retention",
                actor=SystemAuditActor(request_id=None),
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                action="hook_subscription.delete",
                resource_type="hook_subscription",
                resource_id=pinned,
                outcome="success",
                occurred_at=NOW,
                details=None,
            )
        )
    collector = HookRetention(sessions, minimum_age=timedelta(days=1), batch_limit=1)
    for _ in range(4):
        await collector.scan()
    async with short_session(sessions) as database:
        assert await database.get(HookSubscriptionRecord, pinned) is not None
        assert await database.get(HookSubscriptionRecord, collectible) is None


async def test_deleted_inline_head_preserves_noncompleted_source(hook_interaction_sessions):
    sessions = hook_interaction_sessions
    await seed_run_and_secret(sessions)
    head_id, revision_id = await _head(sessions, inline=True)
    collector = HookRetention(sessions, minimum_age=timedelta(days=1), batch_limit=10)
    assert (await collector.scan()).completed == 0
    async with short_session(sessions) as database:
        assert await database.get(HookSubscriptionRecord, head_id) is not None
        assert await database.get(HookSubscriptionRevisionRecord, revision_id) is not None
        assert len(tuple(await database.scalars(select(HookSubscriptionRevisionRecord.id)))) == 1
