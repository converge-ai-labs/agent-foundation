import pytest
from a13n_harness_ui.storage.migration import DatabaseMigrator
from alembic import command
from sqlalchemy import create_engine, inspect, text


def test_live_upgrade_preserves_existing_configuration_and_state(tmp_path):
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "3cf95de9550a"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO thread (thread_id, metadata_version, title, archived, created_at, updated_at, "
                    "initial_state_schema_version, initial_state_digest, continuation_schema_version, continuation_digest) "
                    "VALUES ('existing', 1, 'Retained history', 0, '2026-01-01', '2026-01-01', '1', :initial, '1', :continuation)"
                ),
                {"initial": "a" * 64, "continuation": "b" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO thread_configuration (thread_id, version, agent_source_kind, agent_source_id, "
                    "environment_profile_id, default_model_id, harness_plugin_ids_json, environment_run_extension_ids_json, "
                    "mcp_server_ids_json) VALUES ('existing', 2, 'agent', 'agent-default', "
                    "'environment-native', 'model-existing', '[]', '[]', '[]')"
                )
            )
            before_thread = dict(connection.execute(text("SELECT * FROM thread")).mappings().one())
            before_config = dict(connection.execute(text("SELECT * FROM thread_configuration")).mappings().one())
        migrator.upgrade()
        migrator.upgrade()
        migrator.verify_current()
        with engine.begin() as connection:
            assert dict(connection.execute(text("SELECT * FROM thread")).mappings().one()) == before_thread
            assert (
                dict(connection.execute(text("SELECT * FROM thread_configuration")).mappings().one()) == before_config
            )
            assert connection.execute(text("SELECT count(*) FROM live_transcript_head")).scalar_one() == 0
        # A safe downgrade reverses the new schema without losing ordinary Threads.
        migrator._run(lambda config: command.downgrade(config, "3cf95de9550a"), write=True)
        assert "live_transcript_head" not in inspect(engine).get_table_names()
        migrator.upgrade()
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE thread_configuration SET agent_source_kind = 'live', agent_source_id = 'live'")
            )
        with pytest.raises(RuntimeError, match="Live Threads exist"):
            migrator._run(lambda config: command.downgrade(config, "3cf95de9550a"), write=True)
        migrator.verify_current()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT agent_source_kind FROM thread_configuration")).scalar_one() == "live"
    finally:
        engine.dispose()
