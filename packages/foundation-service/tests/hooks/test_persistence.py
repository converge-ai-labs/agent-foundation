from __future__ import annotations

from datetime import timedelta

import pytest
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.hooks import InlineHookSubscriptionInput, WebhookDestinationConfig
from a13n_service.hooks.models import HookSubscriptionRecord, HookSubscriptionRevisionRecord
from a13n_service.hooks.persistence import HookSubscriptionInvariantError, create_inline_hook_subscription
from a13n_service.interactions.lifecycle import append_run_lifecycle
from a13n_service.interactions.models import RunRecord
from a13n_service.secrets.models import SecretRecord
from a13n_service.storage import short_session, transaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.hooks.support import RUN_ID, SECRET_ID, seed_run_and_secret
from tests.interactions.conftest import NOW, SESSION_ID, TENANT_ID, THREAD_ID, USER_ID, WORKSPACE_ID

pytestmark = pytest.mark.anyio


def _input(*hook_names: str) -> InlineHookSubscriptionInput:
    return InlineHookSubscriptionInput(
        hook_names=hook_names,
        webhook=WebhookDestinationConfig(
            endpoint_url="https://hooks.example.com/foundation",
            signing_secret_id=SECRET_ID,
        ),
    )


async def test_inline_creation_precedes_matching_and_outbox_keeps_exact_revision(
    hook_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_run_and_secret(hook_interaction_sessions)
    async with transaction(hook_interaction_sessions) as database:
        run = await database.get(RunRecord, RUN_ID)
        assert run is not None
        subscription = await create_inline_hook_subscription(
            database,
            organization_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            session_id=SESSION_ID,
            thread_id=THREAD_ID,
            run_id=RUN_ID,
            actor_type="user",
            actor_id=USER_ID,
            subscription=_input("run.accepted", "run.completed"),
            now=NOW,
        )
        await append_run_lifecycle(
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
            organization_id=TENANT_ID,
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
            organization_id=TENANT_ID,
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
        await append_run_lifecycle(
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
                organization_id=TENANT_ID,
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
                organization_id=TENANT_ID,
                workspace_id=WORKSPACE_ID,
                session_id=SESSION_ID,
                thread_id=THREAD_ID,
                run_id=RUN_ID,
                actor_type="user",
                actor_id=USER_ID,
                subscription=_input("run.accepted"),
                now=NOW,
            )
            await append_run_lifecycle(
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
    hook_postgres_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_run_and_secret(hook_postgres_sessions)
    async with transaction(hook_postgres_sessions) as database:
        run = await database.get(RunRecord, RUN_ID)
        assert run is not None
        subscription = await create_inline_hook_subscription(
            database,
            organization_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            session_id=SESSION_ID,
            thread_id=THREAD_ID,
            run_id=RUN_ID,
            actor_type="user",
            actor_id=USER_ID,
            subscription=_input("run.accepted"),
            now=NOW,
        )
        await append_run_lifecycle(
            database,
            run,
            "run.accepted",
            mutation_id="mut_7575757575757575",
            occurred_at=NOW,
            actor_type="user",
            actor_id=USER_ID,
        )

    async with short_session(hook_postgres_sessions) as database:
        delivery = await database.scalar(select(OutboxRecord))
        assert delivery is not None
        assert delivery.destination_ref == subscription.current_revision_id
