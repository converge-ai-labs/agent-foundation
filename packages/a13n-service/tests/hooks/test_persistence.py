from __future__ import annotations

from datetime import timedelta

import pytest
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.hooks import InlineHookSubscriptionInput, WebhookDestinationConfig
from a13n_service.hooks.models import HookSubscriptionRecord, HookSubscriptionRevisionRecord
from a13n_service.hooks.persistence import (
    HookSubscriptionInvariantError,
    create_hook_subscription,
    create_inline_hook_subscription,
)
from a13n_service.interactions.models import RunRecord
from a13n_service.secrets.models import SecretRecord
from a13n_service.storage import short_session, transaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.hooks.support import RUN_ID, SECRET_ID, seed_run_and_secret
from tests.interactions.conftest import NOW, ORGANIZATION_ID, SESSION_ID, THREAD_ID, USER_ID, WORKSPACE_ID
from tests.lifecycle_support import test_lifecycle_writer

pytestmark = pytest.mark.anyio


def _input(*hook_names: str) -> InlineHookSubscriptionInput:
    return InlineHookSubscriptionInput(
        hook_names=hook_names,
        webhook=WebhookDestinationConfig(
            endpoint_url="https://hooks.example.com/foundation",
            signing_secret_id=SECRET_ID,
        ),
    )


async def test_managed_revision_change_preserves_matching_and_outbox_keeps_exact_revision(
    hook_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_run_and_secret(hook_interaction_sessions)
    async with transaction(hook_interaction_sessions) as database:
        run = await database.get(RunRecord, RUN_ID)
        assert run is not None
        subscription = await create_hook_subscription(
            database,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            actor_type="user",
            actor_id=USER_ID,
            subscription=_input("run.accepted", "run.completed").bind_run_scope(
                session_id=SESSION_ID,
                thread_id=THREAD_ID,
                run_id=RUN_ID,
            ),
            now=NOW,
        )
        await test_lifecycle_writer().append_run_lifecycle(
            database,
            run,
            "run.accepted",
            mutation_id="mut_7171717171717171",
            occurred_at=NOW,
            actor_type="user",
            actor_id=USER_ID,
        )
        original_revision_id = subscription.current_revision_id

    async with transaction(hook_interaction_sessions) as database:
        head = await database.get(HookSubscriptionRecord, subscription.id, with_for_update=True)
        assert head is not None
        replacement = HookSubscriptionRevisionRecord(
            id="hsubr_7272727272727272",
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            hook_subscription_id=head.id,
            version=2,
            hook_names=["run.completed"],
            session_id=SESSION_ID,
            thread_id=THREAD_ID,
            run_id=RUN_ID,
            endpoint_url="https://new.example.com/hook",
            signing_secret_id=SECRET_ID,
            signature_profile="hmac_sha256_v1",
            created_by_type="user",
            created_by_id=USER_ID,
            created_at=NOW + timedelta(seconds=1),
        )
        database.add(replacement)
        head.version = 2
        head.current_revision_id = replacement.id
        head.updated_at = NOW + timedelta(seconds=1)

    async with short_session(hook_interaction_sessions) as database:
        deliveries = (await database.scalars(select(OutboxRecord))).all()
        assert len(deliveries) == 1
        assert deliveries[0].source_kind == "lifecycle_event"
        assert deliveries[0].destination_kind == "webhook"
        assert deliveries[0].destination_ref == original_revision_id
        assert deliveries[0].status == "pending"


async def test_scope_name_and_head_state_are_conjunctive(
    hook_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_run_and_secret(hook_interaction_sessions)
    async with transaction(hook_interaction_sessions) as database:
        run = await database.get(RunRecord, RUN_ID)
        assert run is not None
        disabled = await create_inline_hook_subscription(
            database,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            session_id=SESSION_ID,
            thread_id=THREAD_ID,
            run_id=RUN_ID,
            actor_type="user",
            actor_id=USER_ID,
            subscription=_input("run.completed"),
            now=NOW,
        )
        disabled.enabled = False
        await test_lifecycle_writer().append_run_lifecycle(
            database,
            run,
            "run.accepted",
            mutation_id="mut_7373737373737373",
            occurred_at=NOW,
            actor_type="user",
            actor_id=USER_ID,
        )

    async with short_session(hook_interaction_sessions) as database:
        assert tuple((await database.scalars(select(OutboxRecord))).all()) == ()


async def test_inline_creation_requires_current_workspace_secret(
    hook_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_run_and_secret(hook_interaction_sessions)
    async with transaction(hook_interaction_sessions) as database:
        secret = await database.get(SecretRecord, SECRET_ID)
        assert secret is not None
        secret.deleted_at = NOW
        secret.ciphertext = None
        secret.nonce = None
        secret.encryption_key_id = None

    with pytest.raises(HookSubscriptionInvariantError, match="Secret is unavailable"):
        async with transaction(hook_interaction_sessions) as database:
            await create_inline_hook_subscription(
                database,
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                session_id=SESSION_ID,
                thread_id=THREAD_ID,
                run_id=RUN_ID,
                actor_type="user",
                actor_id=USER_ID,
                subscription=_input("run.accepted"),
                now=NOW,
            )


async def test_outbox_rolls_back_with_lifecycle_and_state_transaction(
    hook_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_run_and_secret(hook_interaction_sessions)
    with pytest.raises(RuntimeError, match="abort"):
        async with transaction(hook_interaction_sessions) as database:
            run = await database.get(RunRecord, RUN_ID)
            assert run is not None
            await create_inline_hook_subscription(
                database,
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                session_id=SESSION_ID,
                thread_id=THREAD_ID,
                run_id=RUN_ID,
                actor_type="user",
                actor_id=USER_ID,
                subscription=_input("run.accepted"),
                now=NOW,
            )
            await test_lifecycle_writer().append_run_lifecycle(
                database,
                run,
                "run.accepted",
                mutation_id="mut_7474747474747474",
                occurred_at=NOW,
                actor_type="user",
                actor_id=USER_ID,
            )
            raise RuntimeError("abort")

    async with short_session(hook_interaction_sessions) as database:
        assert await database.scalar(select(HookSubscriptionRecord.id)) is None
        assert await database.scalar(select(OutboxRecord.id)) is None


async def test_postgresql_enforces_current_revision_and_matches_with_jsonb_gin(
    hook_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_run_and_secret(hook_interaction_sessions)
    async with transaction(hook_interaction_sessions) as database:
        run = await database.get(RunRecord, RUN_ID)
        assert run is not None
        subscription = await create_inline_hook_subscription(
            database,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            session_id=SESSION_ID,
            thread_id=THREAD_ID,
            run_id=RUN_ID,
            actor_type="user",
            actor_id=USER_ID,
            subscription=_input("run.accepted"),
            now=NOW,
        )
        await test_lifecycle_writer().append_run_lifecycle(
            database,
            run,
            "run.accepted",
            mutation_id="mut_7575757575757575",
            occurred_at=NOW,
            actor_type="user",
            actor_id=USER_ID,
        )

    async with short_session(hook_interaction_sessions) as database:
        delivery = await database.scalar(select(OutboxRecord))
        assert delivery is not None
        assert delivery.destination_ref == subscription.current_revision_id


@pytest.mark.parametrize("head_state", ["active", "paused", "deleted"])
async def test_failure_before_first_attempt_expires_even_inactive_inline_heads(hook_interaction_sessions, head_state):
    from a13n_service.interactions.scheduling import AttemptScheduler, SealedClaim

    from tests.interactions.test_attempt_execution import _worker

    sessions = hook_interaction_sessions
    await seed_run_and_secret(sessions)
    async with transaction(sessions) as database:
        run = await database.get(RunRecord, RUN_ID)
        run.execution_deadline_at = NOW
        head = await create_inline_hook_subscription(
            database,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            session_id=SESSION_ID,
            thread_id=THREAD_ID,
            run_id=RUN_ID,
            actor_type="user",
            actor_id=USER_ID,
            subscription=_input("run.failed"),
            now=NOW,
        )
        head.enabled = head_state != "paused"
        head.deleted_at = NOW if head_state == "deleted" else None
    result = await AttemptScheduler(
        sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    ).claim(RUN_ID, _worker())
    assert isinstance(result, SealedClaim)
    async with short_session(sessions) as database:
        failed = await database.get(RunRecord, RUN_ID)
        expired = await database.get(HookSubscriptionRecord, head.id)
        assert failed.status == "failed" and failed.attempts_started == 0
        assert expired.expired_at == failed.sealed_at
        assert expired.enabled == (head_state != "paused")
        assert (expired.deleted_at is not None) == (head_state == "deleted")
        deliveries = (await database.scalars(select(OutboxRecord))).all()
        assert len(deliveries) == (1 if head_state == "active" else 0)


async def test_failed_sealing_transaction_rolls_back_inline_expiry(hook_interaction_sessions):
    from a13n_service.hooks.persistence import write_hook_lifecycle
    from a13n_service.interactions.lifecycle import LifecycleWriter
    from a13n_service.interactions.scheduling import AttemptScheduler
    from a13n_service.lifecycle.models import LifecycleEventRecord

    from tests.interactions.test_attempt_execution import _worker

    sessions = hook_interaction_sessions
    await seed_run_and_secret(sessions)
    async with transaction(sessions) as database:
        run = await database.get(RunRecord, RUN_ID)
        run.execution_deadline_at = NOW
        head = await create_inline_hook_subscription(
            database,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            session_id=SESSION_ID,
            thread_id=THREAD_ID,
            run_id=RUN_ID,
            actor_type="user",
            actor_id=USER_ID,
            subscription=_input("run.failed"),
            now=NOW,
        )

    async def abort_after_hooks(database, event):
        expired = await database.get(HookSubscriptionRecord, head.id)
        assert expired.expired_at is not None
        raise RuntimeError("abort sealing")

    scheduler = AttemptScheduler(
        sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        lifecycle=LifecycleWriter((write_hook_lifecycle, abort_after_hooks)),
    )
    with pytest.raises(RuntimeError, match="abort sealing"):
        await scheduler.claim(RUN_ID, _worker())
    async with short_session(sessions) as database:
        run = await database.get(RunRecord, RUN_ID)
        retained = await database.get(HookSubscriptionRecord, head.id)
        assert run.status == "accepted" and run.sealed_at is None
        assert retained.expired_at is None and retained.enabled
        assert await database.scalar(select(OutboxRecord.id)) is None
        assert await database.scalar(select(LifecycleEventRecord.id)) is None
