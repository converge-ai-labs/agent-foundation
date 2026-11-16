"""Connector upgrades preserve accounts and backfill unique readable tool groups."""

import pytest
from a13n_harness_ui.storage.migration import DatabaseMigrator
from alembic import command
from sqlalchemy import create_engine, inspect, text


@pytest.mark.parametrize(
    "starting_revision", ["2e9f82450690", "f196302d868f", "9450d8b1a589", "7f64c893cc4a", "b816fd5edbe8"]
)
def test_connector_upgrade_preserves_private_values_and_defaults(tmp_path, starting_revision):
    has_external_context = starting_revision != "2e9f82450690"
    has_read_state = starting_revision in {"9450d8b1a589", "7f64c893cc4a", "b816fd5edbe8"}
    has_thread_inbox = starting_revision in {"7f64c893cc4a", "b816fd5edbe8"}
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, starting_revision), write=True)
    engine = create_engine(f"sqlite:///{path}")
    insert = text(
        "INSERT INTO connector_configuration "
        "(connector_id, kind, enabled, deliver_to_bot, selected_events, settings, credentials) "
        'VALUES (:id, \'example\', 1, 1, \'["changed"]\', \'{"scope":"demo"}\', \'{"token":"private-fixture"}\')'
    )
    try:
        with engine.begin() as connection:
            connection.execute(insert, {"id": "existing"})
            connection.execute(
                text(
                    "INSERT INTO thread (thread_id, metadata_version, title, archived, created_at, updated_at, "
                    "initial_state_schema_version, initial_state_digest, continuation_schema_version, continuation_digest) "
                    "VALUES ('retained-thread', 1, 'Retained', 0, '2026-01-01', '2026-01-01', '1', :digest, '1', :digest)"
                ),
                {"digest": "a" * 64},
            )
            if has_external_context:
                connection.execute(text('UPDATE thread SET external_context_json = \'{"source":"retained"}\''))
            if has_read_state:
                connection.execute(text("UPDATE thread SET unread = 1, read_state_version = 7"))
            if has_thread_inbox:
                connection.execute(
                    text(
                        "INSERT INTO bot_inbox (input_id, source, source_thread_id, source_thread_title, event_type, "
                        "source_event_id, text, created_at) VALUES ('retained-report', 'thread', 'retained-thread', "
                        "'Retained', 'report', 'retained-report', 'Verified progress', '2026-11-12')"
                    )
                )
        migrator.upgrade()
        migrator.verify_current()
        with engine.begin() as connection:
            assert connection.execute(text("SELECT external_context_json FROM thread")).scalar_one() == (
                '{"source":"retained"}' if has_external_context else None
            )
            assert connection.execute(text("SELECT unread, read_state_version FROM thread")).one() == (
                (1, 7) if has_read_state else (0, 0)
            )
            if has_thread_inbox:
                assert connection.execute(
                    text("SELECT source_thread_id, source_thread_title, text FROM bot_inbox")
                ).one() == ("retained-thread", "Retained", "Verified progress")
            rows = (
                connection.execute(text("SELECT * FROM connector_configuration ORDER BY connector_id")).mappings().all()
            )
            for row in rows:
                assert row["credentials"] == '{"token":"private-fixture"}'
                assert row["settings"] == '{"scope":"demo"}'
                assert row["selected_events"] == '["changed"]'
                assert row["version"] == row["credential_version"] == 1
                assert row["name"] == ""
                assert row["group_name"] == "example"
                assert row["rules"] == "[]"
                assert row["deleted"] == 0
        inspector = inspect(engine)
        assert "connector_state" in inspector.get_table_names()
        assert "ix_connector_setup_offer_thread_id" in {
            index["name"] for index in inspector.get_indexes("connector_setup_offer")
        }
        migrator.upgrade()
        migrator.verify_current()
    finally:
        engine.dispose()


def test_group_backfill_preserves_accounts_relations_and_resolves_collisions(tmp_path):
    from a13n_harness_ui.connector_models import ConnectorGroupName
    from pydantic import TypeAdapter
    from sqlalchemy.exc import IntegrityError

    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "b816fd5edbe8"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    names = ["Work GitHub", "Work...GitHub", "work-github-2", "", "私人账户", "123", "a" * 32 + "x", "a" * 32 + "y"]
    try:
        with engine.begin() as connection:
            for index, name in enumerate(names):
                connection.execute(
                    text(
                        "INSERT INTO connector_configuration "
                        "(connector_id, kind, name, enabled, deliver_to_bot, selected_events, settings, credentials, version, credential_version, rules) "
                        "VALUES (:id, 'github', :name, 0, 1, '[\"changed\"]', '{\"scope\":\"demo\"}', '{\"token\":\"private-fixture\"}', 7, 3, '[]')"
                    ),
                    {"id": f"account-{index}", "name": name},
                )
            connection.execute(
                text(
                    "INSERT INTO connector_state (connector_id, key, version, value) VALUES ('account-0', 'cursor', 4, :value)"
                ),
                {"value": '{"position":12}'},
            )
            connection.execute(
                text(
                    "INSERT INTO bot_inbox (input_id, source, connector_id, event_type, source_event_id, text, created_at) "
                    "VALUES ('input-retained', 'connector', 'account-0', 'changed', 'external-1', 'Retained input', '2026-11-12')"
                )
            )
            before = (
                connection.execute(text("SELECT * FROM connector_configuration ORDER BY connector_id")).mappings().all()
            )
        migrator.upgrade()
        with engine.connect() as connection:
            after = (
                connection.execute(text("SELECT * FROM connector_configuration ORDER BY connector_id")).mappings().all()
            )
            assert [{key: row[key] for key in before[0]} for row in after] == [dict(row) for row in before]
            groups = [row["group_name"] for row in after]
            assert groups == [
                "work-github",
                "work-github-2",
                "work-github-2-2",
                "github",
                "github-2",
                "connection-123",
                "a" * 32,
                "a" * 30 + "-2",
            ]
            for group in groups:
                TypeAdapter(ConnectorGroupName).validate_python(group)
            assert connection.execute(text("SELECT version, value FROM connector_state")).one() == (
                4,
                '{"position":12}',
            )
            assert connection.execute(text("SELECT input_id, connector_id, text FROM bot_inbox")).one() == (
                "input-retained",
                "account-0",
                "Retained input",
            )
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text("UPDATE connector_configuration SET group_name='github' WHERE connector_id='account-0'")
            )
        migrator.upgrade()
        migrator.verify_current()
        with engine.connect() as connection:
            assert (
                list(
                    connection.execute(
                        text("SELECT group_name FROM connector_configuration ORDER BY connector_id")
                    ).scalars()
                )
                == groups
            )
    finally:
        engine.dispose()


def test_group_upgrade_preserves_existing_mcp_oauth_accounts(tmp_path):
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "5304b6a9760a"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO connector_configuration "
                    "(connector_id, kind, name, enabled, deliver_to_bot, selected_events, settings, credentials) "
                    "VALUES ('account', 'github', 'Existing', 1, 0, '[]', '{}', '{}')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO mcp_oauth "
                    "(binding_id, owner, configuration_digest, generation, credential_version, refresh_pending, bundle_json) "
                    "VALUES ('binding', 'connector:account', :digest, 'generation', 4, 0, :bundle)"
                ),
                {"digest": "a" * 64, "bundle": '{"retained":"private-fixture"}'},
            )
        migrator.upgrade()
        migrator.verify_current()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT group_name FROM connector_configuration")).scalar_one() == "existing"
            assert connection.execute(text("SELECT credential_version, bundle_json FROM mcp_oauth")).one() == (
                4,
                '{"retained":"private-fixture"}',
            )
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        migrator.upgrade()
        migrator.verify_current()
    finally:
        engine.dispose()


def test_failed_group_backfill_rolls_back_and_can_be_retried(tmp_path):
    from sqlalchemy.exc import IntegrityError

    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "b816fd5edbe8"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO connector_configuration "
                    "(connector_id, kind, name, enabled, deliver_to_bot, selected_events, settings, credentials) "
                    "VALUES ('account-one', 'github', 'GitHub', 1, 0, '[]', '{}', '{\"token\":\"private-fixture\"}'), "
                    "('account-two', 'github', 'GitHub', 1, 0, '[]', '{}', '{}')"
                )
            )
            # Fail after the first row has been assigned, not before migration starts.
            connection.exec_driver_sql(
                "CREATE TRIGGER fail_group_backfill BEFORE UPDATE ON connector_configuration "
                "WHEN OLD.connector_id = 'account-two' BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
            )
            before = connection.execute(text("SELECT * FROM connector_configuration ORDER BY connector_id")).all()
        with pytest.raises(IntegrityError, match="injected failure"):
            migrator.upgrade()
        with engine.begin() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "b816fd5edbe8"
            assert "group_name" not in {
                column["name"] for column in inspect(connection).get_columns("connector_configuration")
            }
            assert (
                connection.execute(text("SELECT * FROM connector_configuration ORDER BY connector_id")).all() == before
            )
            connection.exec_driver_sql("DROP TRIGGER fail_group_backfill")
        migrator.upgrade()
        migrator.verify_current()
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT group_name FROM connector_configuration ORDER BY connector_id")
            ).scalars().all() == ["github", "github-2"]
    finally:
        engine.dispose()
