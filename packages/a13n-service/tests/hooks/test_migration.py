import pytest
from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.relational import database_url
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DatabaseError


def _assert_hook_schema(config: PostgreSQLConfig, *, present: bool) -> None:
    engine = create_engine(database_url(config))
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        if not present:
            assert "hook_subscriptions" not in tables
            assert "hook_subscription_revisions" not in tables
            return
        assert {"hook_subscriptions", "hook_subscription_revisions"} <= tables
        head_indexes = {index["name"] for index in inspector.get_indexes("hook_subscriptions")}
        revision_indexes = {index["name"]: index for index in inspector.get_indexes("hook_subscription_revisions")}
        assert "ix_hook_subscriptions_active_workspace" in head_indexes
        scope_index_names = {
            "ix_hook_subscription_revisions_hook_names",
            "ix_hook_subscription_revisions_run",
            "ix_hook_subscription_revisions_session",
            "ix_hook_subscription_revisions_thread",
        }
        assert scope_index_names <= revision_indexes.keys()
        for scope in ("run", "session", "thread"):
            options = revision_indexes[f"ix_hook_subscription_revisions_{scope}"]["dialect_options"]
            assert options["postgresql_where"] is not None
        with engine.connect() as connection:
            triggers = set(
                connection.execute(
                    text(
                        "SELECT trigger_name FROM information_schema.triggers "
                        "WHERE event_object_table = 'hook_subscription_revisions'"
                    )
                ).scalars()
            )
        assert {"reject_hook_subscription_revision_update", "validate_hook_subscription_revision_insert"} <= triggers
    finally:
        engine.dispose()


def _exercise_migration(config: PostgreSQLConfig) -> None:
    migrator = DatabaseMigrator(config)
    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    _assert_hook_schema(config, present=True)
    migrator.downgrade("base")
    _assert_hook_schema(config, present=False)


def test_hook_schema_migrates_up_and_down(postgres_database: PostgreSQLConfig) -> None:
    _exercise_migration(postgres_database)


def test_hook_revision_is_immutable(postgres_database: PostgreSQLConfig) -> None:
    config = postgres_database
    DatabaseMigrator(config).upgrade()
    engine = create_engine(database_url(config))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organizations (id, name, key, created_at, updated_at) "
                    "VALUES ('org_1234567890abcdef', 'Test', 'test', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO workspaces "
                    "(id, organization_id, name, key, created_at, updated_at, deleted_at) "
                    "VALUES ('ws_1234567890abcdef', 'org_1234567890abcdef', 'Test', 'test', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO secrets "
                    "(id, organization_id, workspace_id, owner_type, owner_id, key, version, ciphertext, nonce, "
                    "encryption_key_id, created_at, value_updated_at, deleted_at) VALUES "
                    "('sec_1234567890abcdef', 'org_1234567890abcdef', 'ws_1234567890abcdef', 'workspace', "
                    "'ws_1234567890abcdef', 'hook-signing', 1, decode('01','hex'), "
                    "decode('000000000000000000000000','hex'), 'test-key', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO hook_subscriptions "
                    "(id, organization_id, workspace_id, version, current_revision_id, enabled, inline_run_id, "
                    "deleted_at, created_by_type, created_by_id, updated_by_type, updated_by_id, created_at, updated_at) "
                    "VALUES ('hsub_1234567890abcdef', 'org_1234567890abcdef', 'ws_1234567890abcdef', 1, "
                    "'hsubr_1234567890abcdef', TRUE, NULL, NULL, 'user', 'usr_1234567890abcdef', "
                    "'user', 'usr_1234567890abcdef', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO hook_subscription_revisions "
                    "(id, organization_id, workspace_id, hook_subscription_id, version, hook_names, session_id, "
                    "thread_id, run_id, endpoint_url, signing_secret_id, signature_profile, created_by_type, "
                    "created_by_id, created_at) VALUES ('hsubr_1234567890abcdef', 'org_1234567890abcdef', "
                    "'ws_1234567890abcdef', 'hsub_1234567890abcdef', 1, '[\"run.accepted\"]', NULL, NULL, NULL, "
                    "'https://example.com/hook', 'sec_1234567890abcdef', 'hmac_sha256_v1', 'user', "
                    "'usr_1234567890abcdef', CURRENT_TIMESTAMP)"
                )
            )
        with pytest.raises(DatabaseError, match="immutable"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE hook_subscription_revisions SET endpoint_url = 'https://other.example/hook' "
                        "WHERE id = 'hsubr_1234567890abcdef'"
                    )
                )
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_inline_database_guards_after_clean_migration(postgres_database):
    from a13n_service.hooks import InlineHookSubscriptionInput, WebhookDestinationConfig
    from a13n_service.hooks.models import HookSubscriptionRecord, HookSubscriptionRevisionRecord
    from a13n_service.hooks.persistence import create_hook_subscription, create_inline_hook_subscription
    from a13n_service.storage import short_session, transaction
    from a13n_service.storage.relational import create_session_factory, create_sql_engine
    from sqlalchemy import update

    from tests.hooks.support import RUN_ID, SECRET_ID, seed_run_and_secret
    from tests.interactions.conftest import (
        NOW,
        ORGANIZATION_ID,
        SESSION_ID,
        THREAD_ID,
        USER_ID,
        WORKSPACE_ID,
        _seed_interaction_database,
    )

    config = postgres_database
    migrator = DatabaseMigrator(config)
    migrator.upgrade()
    engine = create_sql_engine(config)
    sessions = create_session_factory(engine)
    try:
        await _seed_interaction_database(sessions)
        await seed_run_and_secret(sessions)
        subscription = InlineHookSubscriptionInput(
            hook_names=("run.accepted",),
            webhook=WebhookDestinationConfig(endpoint_url="https://example.com/hooks", signing_secret_id=SECRET_ID),
        )
        # Initial inline scope must include the exact owning Run, Session and Thread.
        for scope in ("run_id", "session_id", "thread_id"):
            with pytest.raises(DatabaseError, match="immutable"):
                async with transaction(sessions) as database:
                    await create_hook_subscription(
                        database,
                        organization_id=ORGANIZATION_ID,
                        workspace_id=WORKSPACE_ID,
                        inline_run_id=RUN_ID,
                        actor_type="user",
                        actor_id=USER_ID,
                        now=NOW,
                        subscription=subscription.bind_run_scope(
                            session_id=SESSION_ID,
                            thread_id=THREAD_ID,
                            run_id=RUN_ID,
                        ).model_copy(update={scope: None}),
                    )
        async with transaction(sessions) as database:
            head = await create_inline_hook_subscription(
                database,
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                session_id=SESSION_ID,
                thread_id=THREAD_ID,
                run_id=RUN_ID,
                actor_type="user",
                actor_id=USER_ID,
                subscription=subscription,
                now=NOW,
            )
        async with short_session(sessions) as database:
            revision = await database.get(HookSubscriptionRevisionRecord, head.current_revision_id)
            original = {
                column.name: getattr(revision, column.name)
                for column in HookSubscriptionRevisionRecord.__table__.columns
            }
        # Appending another Revision is forbidden even without advancing the head.
        with pytest.raises(DatabaseError, match="immutable"):
            async with transaction(sessions) as database:
                database.add(
                    HookSubscriptionRevisionRecord(**{**original, "id": "hsubr_8181818181818181", "version": 2})
                )
        for change in ({"inline_run_id": None}, {"current_revision_id": "hsubr_8181818181818181"}):
            with pytest.raises(DatabaseError, match="immutable"):
                async with transaction(sessions) as database:
                    await database.execute(
                        update(HookSubscriptionRecord).where(HookSubscriptionRecord.id == head.id).values(**change)
                    )
        async with transaction(sessions) as database:
            await database.execute(
                update(HookSubscriptionRecord)
                .where(HookSubscriptionRecord.id == head.id)
                .values(expired_at=NOW, enabled=False)
            )
        with pytest.raises(DatabaseError, match="immutable"):
            async with transaction(sessions) as database:
                await database.execute(
                    update(HookSubscriptionRecord).where(HookSubscriptionRecord.id == head.id).values(expired_at=None)
                )
        async with transaction(sessions) as database:
            await database.execute(
                update(HookSubscriptionRecord)
                .where(HookSubscriptionRecord.id == head.id)
                .values(enabled=True, deleted_at=NOW)
            )
    finally:
        await engine.dispose()
