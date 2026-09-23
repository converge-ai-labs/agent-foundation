from a13n_harness_ui.storage.migration import DatabaseMigrator
from alembic import command
from sqlalchemy import create_engine, inspect, text


def test_upgrade_starts_without_workers_and_downgrade_preserves_conversations(tmp_path):
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "027c7c879425"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            for thread_id in ("lead", "old-worker", "ordinary"):
                connection.execute(
                    text(
                        "INSERT INTO thread (thread_id, metadata_version, archived, created_at, updated_at, "
                        "initial_state_schema_version, initial_state_digest) "
                        "VALUES (:id, 1, 0, '2026-01-01', '2026-01-01', '1', :digest)"
                    ),
                    {"id": thread_id, "digest": "a" * 64},
                )
            connection.execute(
                text("INSERT INTO project_lead (project_id, thread_id, enabled) VALUES ('project', 'lead', 1)")
            )
        migrator.upgrade()
        migrator.verify_current()
        with engine.begin() as connection:
            assert connection.execute(text("SELECT count(*) FROM project_lead_worker")).scalar_one() == 0
            assert connection.execute(text("SELECT thread_id, enabled FROM project_lead")).one() == ("lead", 1)
            connection.execute(
                text("INSERT INTO project_lead_worker (worker_thread_id, lead_thread_id) VALUES ('old-worker', 'lead')")
            )
        migrator._run(lambda config: command.downgrade(config, "027c7c879425"), write=True)
        assert "project_lead_worker" not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM thread")).scalar_one() == 3
            assert (
                connection.execute(
                    text("SELECT initial_state_digest FROM thread WHERE thread_id = 'old-worker'")
                ).scalar_one()
                == "a" * 64
            )
        migrator.upgrade()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM project_lead_worker")).scalar_one() == 0
    finally:
        engine.dispose()
