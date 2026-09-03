from pathlib import Path

import pytest
from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.relational import sync_database_url
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DatabaseError


def _assert_hook_schema(config: PostgreSQLConfig | SQLiteConfig, *, present: bool) -> None:
    engine = create_engine(sync_database_url(config))
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        if not present:
            assert "hook_subscriptions" not in tables
            assert "hook_subscription_revisions" not in tables
            return
        assert {"hook_subscriptions", "hook_subscription_revisions"} <= tables
        head_indexes = {index["name"] for index in inspector.get_indexes("hook_subscriptions")}
        revision_indexes = {index["name"] for index in inspector.get_indexes("hook_subscription_revisions")}
        assert "ix_hook_subscriptions_active_workspace" in head_indexes
        assert {
            "ix_hook_subscription_revisions_hook_names",
            "ix_hook_subscription_revisions_run",
            "ix_hook_subscription_revisions_session",
            "ix_hook_subscription_revisions_thread",
        } <= revision_indexes
        with engine.connect() as connection:
            if connection.dialect.name == "postgresql":
                triggers = set(
                    connection.execute(
                        text(
                            "SELECT trigger_name FROM information_schema.triggers "
                            "WHERE event_object_table = 'hook_subscription_revisions'"
                        )
                    ).scalars()
                )
            else:
                triggers = set(
                    connection.execute(
                        text(
                            "SELECT name FROM sqlite_master "
                            "WHERE type = 'trigger' AND tbl_name = 'hook_subscription_revisions'"
                        )
                    ).scalars()
                )
        assert {
            "reject_hook_subscription_revision_update",
            "validate_hook_subscription_revision_insert",
        } <= triggers
    finally:
        engine.dispose()


def _exercise_migration(config: PostgreSQLConfig | SQLiteConfig) -> None:
    migrator = DatabaseMigrator(config)
    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    _assert_hook_schema(config, present=True)
    migrator.downgrade("base")
    _assert_hook_schema(config, present=False)


def test_hook_schema_migrates_up_and_down_on_sqlite(tmp_path: Path) -> None:
    _exercise_migration(SQLiteConfig(path=tmp_path / "hook-migrations.sqlite3"))


def test_hook_schema_migrates_up_and_down_on_postgresql(pg_url: str) -> None:
    _exercise_migration(PostgreSQLConfig(url=pg_url))


def test_hook_revision_is_immutable_in_sqlite(tmp_path: Path) -> None:
    config = SQLiteConfig(path=tmp_path / "hook-immutability.sqlite3")
    DatabaseMigrator(config).upgrade()
    engine = create_engine(sync_database_url(config))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organizations (id, name, created_at, updated_at) "
                    "VALUES ('org_1234567890abcdef', 'Test', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO workspaces "
                    "(id, organization_id, name, normalized_name, created_at, updated_at, deleted_at) "
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
                    "'ws_1234567890abcdef', 'hook-signing', 1, X'01', X'000000000000000000000000', 'test-key', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO hook_subscriptions "
                    "(id, organization_id, workspace_id, version, current_revision_id, enabled, inline_run_id, "
                    "deleted_at, created_by_type, created_by_id, updated_by_type, updated_by_id, created_at, updated_at) "
                    "VALUES ('hsub_1234567890abcdef', 'org_1234567890abcdef', 'ws_1234567890abcdef', 1, "
                    "'hsubr_1234567890abcdef', 1, NULL, NULL, 'user', 'usr_1234567890abcdef', "
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
