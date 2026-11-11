"""Connector upgrade preserves existing private configuration and old insert defaults."""

from a13n_harness_ui.storage.migration import DatabaseMigrator
from alembic import command
from sqlalchemy import create_engine, inspect, text


def test_connector_upgrade_preserves_private_values_and_defaults(tmp_path):
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "2e9f82450690"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    insert = text(
        "INSERT INTO connector_configuration "
        "(connector_id, kind, enabled, deliver_to_bot, selected_events, settings, credentials) "
        'VALUES (:id, \'example\', 1, 1, \'["changed"]\', \'{"scope":"demo"}\', \'{"token":"private-fixture"}\')'
    )
    try:
        with engine.begin() as connection:
            connection.execute(insert, {"id": "existing"})
        migrator.upgrade()
        migrator.verify_current()
        with engine.begin() as connection:
            connection.execute(insert, {"id": "old-writer"})
            rows = (
                connection.execute(text("SELECT * FROM connector_configuration ORDER BY connector_id")).mappings().all()
            )
            for row in rows:
                assert row["credentials"] == '{"token":"private-fixture"}'
                assert row["settings"] == '{"scope":"demo"}'
                assert row["selected_events"] == '["changed"]'
                assert row["version"] == row["credential_version"] == 1
                assert row["name"] == ""
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
