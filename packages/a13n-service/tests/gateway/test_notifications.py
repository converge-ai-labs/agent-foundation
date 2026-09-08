from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Literal

import pytest
from a13n_service.gateway.notifications import NotificationError, NotificationService, NotificationSubscription
from a13n_service.interactions.models import RunRecord
from a13n_service.storage import transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.hooks.support import ORGANIZATION_ID, RUN_ID, hook_actor, seed_hook_actor_access, seed_run_and_secret
from tests.interactions.conftest import NOW, THREAD_ID
from tests.lifecycle_support import test_lifecycle_writer

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("browser_session", [False, True])
@pytest.mark.parametrize("scope", ["thread", "workspace"])
async def test_notification_subscription_delivers_only_future_metadata(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    browser_session: bool,
    scope: Literal["thread", "workspace"],
) -> None:
    await seed_run_and_secret(lifecycle_interaction_sessions)
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service = NotificationService(lifecycle_interaction_sessions)
    actor = hook_actor()
    if browser_session:
        actor = replace(
            actor, auth_method="session", boundary_workspace_id=None, boundary_organization_id=ORGANIZATION_ID
        )
    (subscription,) = await service.authorize(
        actor=actor,
        subscriptions=(
            NotificationSubscription(
                subscription_id="sub-1",
                scope=scope,
                resource_id=THREAD_ID if scope == "thread" else hook_actor().workspace_id,
                topics=("run.updated", "thread.updated"),
            ),
        ),
    )
    async with transaction(lifecycle_interaction_sessions) as database:
        run = await database.get(RunRecord, RUN_ID)
        assert run is not None
        await test_lifecycle_writer().append_run_lifecycle(
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


@pytest.mark.parametrize("scope", ["thread", "workspace"])
@pytest.mark.parametrize("browser_session", [False, True])
async def test_notification_subscription_conceals_other_credential_boundaries(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    scope: Literal["thread", "workspace"],
    browser_session: bool,
) -> None:
    await seed_run_and_secret(lifecycle_interaction_sessions)
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    actor = hook_actor()
    actor = (
        replace(actor, auth_method="session", boundary_workspace_id=None, boundary_organization_id="org_other")
        if browser_session
        else replace(actor, boundary_workspace_id="ws_other")
    )
    with pytest.raises(NotificationError) as captured:
        await NotificationService(lifecycle_interaction_sessions).authorize(
            actor=actor,
            subscriptions=(
                NotificationSubscription(
                    subscription_id="sub-other",
                    scope=scope,
                    resource_id=THREAD_ID if scope == "thread" else hook_actor().workspace_id,
                    topics=("run.updated",),
                ),
            ),
        )
    assert captured.value.code == "resource_not_found"
