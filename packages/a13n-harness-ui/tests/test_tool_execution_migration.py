from a13n_harness_ui.storage.migration import DatabaseMigrator
from alembic import command
from sqlalchemy import create_engine, inspect, text


def test_tool_execution_migration_preserves_threads_and_roundtrips_empty_schema(tmp_path):
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "1f69a2ed231f"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO thread (thread_id, metadata_version, title, archived, created_at, updated_at, "
                    "initial_state_schema_version, initial_state_digest) "
                    "VALUES ('existing', 1, 'Retained history', 0, '2026-01-01', '2026-01-01', '1', :initial)"
                ),
                {"initial": "a" * 64},
            )
            before = dict(connection.execute(text("SELECT * FROM thread")).mappings().one())
        migrator.upgrade()
        migrator.upgrade()
        migrator.verify_current()
        with engine.begin() as connection:
            assert dict(connection.execute(text("SELECT * FROM thread")).mappings().one()) == before
            assert connection.execute(text("SELECT count(*) FROM tool_execution")).scalar_one() == 0
            assert connection.execute(text("SELECT count(*) FROM tool_completion_suppression")).scalar_one() == 0
        columns = {column["name"]: column for column in inspect(engine).get_columns("tool_execution")}
        assert columns["payload_digest"]["nullable"]
        assert columns["consumed_at"]["nullable"] and columns["expires_at"]["nullable"]
        migrator._run(lambda config: command.downgrade(config, "1f69a2ed231f"), write=True)
        assert "tool_execution" not in inspect(engine).get_table_names()
        migrator.upgrade()
        with engine.connect() as connection:
            assert dict(connection.execute(text("SELECT * FROM thread")).mappings().one()) == before
    finally:
        engine.dispose()
