import pytest
from a13n_harness_ui.storage.migration import DatabaseMigrator
from alembic import command
from sqlalchemy import create_engine, inspect, text


def test_thread_inbox_upgrade_preserves_pending_input_and_guards_downgrade(tmp_path):
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "9450d8b1a589"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO bot_inbox (input_id, source, event_type, source_event_id, text, created_at) VALUES ('tick', 'heartbeat', 'heartbeat', 'tick', 'Check ongoing work', '2026-11-12')"
                )
            )
        migrator.upgrade()
        migrator.upgrade()
        migrator.verify_current()
        with engine.begin() as connection:
            assert connection.execute(text("SELECT input_id, text, source_thread_id FROM bot_inbox")).all() == [
                ("tick", "Check ongoing work", None)
            ]
            connection.execute(
                text(
                    "INSERT INTO bot_inbox (input_id, source, source_thread_id, source_thread_title, event_type, source_event_id, text, created_at) VALUES ('report', 'thread', 'thread-work', 'Work', 'report', 'report', 'Verified', '2026-11-12')"
                )
            )
        with pytest.raises(RuntimeError, match="Deliver pending Thread messages"):
            migrator._run(lambda config: command.downgrade(config, "9450d8b1a589"), write=True)
        with engine.begin() as connection:
            assert connection.execute(text("SELECT count(*) FROM bot_inbox")).scalar_one() == 2
            connection.execute(text("DELETE FROM bot_inbox WHERE input_id = 'report'"))
        migrator._run(lambda config: command.downgrade(config, "9450d8b1a589"), write=True)
        assert "source_thread_id" not in {column["name"] for column in inspect(engine).get_columns("bot_inbox")}
        migrator.upgrade()
        migrator.verify_current()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT input_id FROM bot_inbox")).all() == [("tick",)]
    finally:
        engine.dispose()
