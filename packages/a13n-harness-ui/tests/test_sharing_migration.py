"""Adding grants preserves existing Thread data and supports an empty downgrade."""

from a13n_harness_ui.storage.migration import DatabaseMigrator
from alembic import command
from sqlalchemy import create_engine, inspect, text


def test_sharing_upgrade_preserves_existing_threads(tmp_path):
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "cb56f9a0a4df"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO thread (thread_id, metadata_version, title, archived, created_at, updated_at, "
                    "initial_state_schema_version, initial_state_digest) "
                    "VALUES ('existing', 1, 'Retained history', 0, '2026-01-01', '2026-01-01', '1', :digest)"
                ),
                {"digest": "a" * 64},
            )
            before = dict(connection.execute(text("SELECT * FROM thread")).mappings().one())
        migrator.upgrade()
        migrator.upgrade()
        migrator.verify_current()
        with engine.connect() as connection:
            assert dict(connection.execute(text("SELECT * FROM thread")).mappings().one()) == before
            assert connection.execute(text("SELECT count(*) FROM share_grant")).scalar_one() == 0
        migrator._run(lambda config: command.downgrade(config, "cb56f9a0a4df"), write=True)
        assert "share_grant" not in inspect(engine).get_table_names()
        migrator.upgrade()
        migrator.verify_current()
    finally:
        engine.dispose()
