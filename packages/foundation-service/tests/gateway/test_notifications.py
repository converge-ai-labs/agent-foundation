from __future__ import annotations

from datetime import timedelta

import pytest
from a13n_service.gateway.notifications import NotificationError, NotificationService, NotificationSubscription
from a13n_service.interactions.lifecycle import append_run_lifecycle
from a13n_service.interactions.models import RunRecord
from a13n_service.storage import transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.hooks.support import RUN_ID, hook_actor, seed_hook_actor_access, seed_run_and_secret
from tests.interactions.conftest import NOW, THREAD_ID

pytestmark = pytest.mark.anyio


async def test_notification_subscription_delivers_only_future_metadata(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_run_and_secret(lifecycle_interaction_sessions)
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service = NotificationService(lifecycle_interaction_sessions)
    (subscription,) = await service.authorize(
        actor=hook_actor(),
        subscriptions=(
            NotificationSubscription(
                subscription_id="sub-1",
                scope="thread",
                resource_id=THREAD_ID,
                topics=("run.updated", "thread.updated"),
            ),
        ),
    )
    async with transaction(lifecycle_interaction_sessions) as database:
        run = await database.get(RunRecord, RUN_ID)
        assert run is not None
        await append_run_lifecycle(
            database,
            run,
            "run.accepted",
            mutation_id="mut_8181818181818181",
            occurred_at=NOW + timedelta(seconds=1),
            actor_type="user",
            actor_id=hook_actor().principal.principal_id,
        )

    facts = await service.read(subscription, limit=10)

    assert len(facts) == 1
    assert facts[0].run_id == RUN_ID
    assert facts[0].resource_version == 1
    assert not hasattr(facts[0], "payload")


async def test_thread_subscription_rejects_workspace_only_topic(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_run_and_secret(lifecycle_interaction_sessions)
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service = NotificationService(lifecycle_interaction_sessions)

    with pytest.raises(NotificationError) as captured:
        await service.authorize(
            actor=hook_actor(),
            subscriptions=(
                NotificationSubscription(
                    subscription_id="sub-1",
                    scope="thread",
                    resource_id=THREAD_ID,
                    topics=("session.updated",),
                ),
            ),
        )

    assert captured.value.code == "invalid_subscription_topic"
