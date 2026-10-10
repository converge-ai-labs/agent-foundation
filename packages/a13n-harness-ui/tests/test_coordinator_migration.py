from a13n_harness_ui.storage.migration import DatabaseMigrator
from alembic import command
from sqlalchemy import create_engine, inspect, text


def test_upgrade_preserves_coordinators_workers_settings_and_history(tmp_path):
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "78e4e7206898"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            for thread_id in ("lead", "disabled", "worker", "ordinary"):
                connection.execute(
                    text(
                        "INSERT INTO thread (thread_id, metadata_version, title, archived, created_at, updated_at, "
                        "initial_state_schema_version, initial_state_digest, continuation_schema_version, continuation_digest) "
                        "VALUES (:id, 1, :id, 0, '2026-01-01', '2026-01-01', '1', :initial, '1', :continuation)"
                    ),
                    {"id": thread_id, "initial": "a" * 64, "continuation": "b" * 64},
                )
            connection.execute(
                text(
                    "INSERT INTO project_lead (project_id, thread_id, enabled) VALUES ('project', 'lead', 1), ('other', 'disabled', 0)"
                )
            )
            connection.execute(
                text("INSERT INTO project_lead_worker (worker_thread_id, lead_thread_id) VALUES ('worker', 'lead')")
            )
            before = connection.execute(text("SELECT * FROM thread ORDER BY thread_id")).mappings().all()
        migrator.upgrade()
        migrator.upgrade()
        migrator.verify_current()
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT thread_id, auto_followup FROM coordinator ORDER BY thread_id")
            ).all() == [("disabled", 0), ("lead", 1)]
            assert connection.execute(
                text("SELECT worker_thread_id, coordinator_thread_id FROM coordinator_worker")
            ).all() == [("worker", "lead")]
            after = connection.execute(text("SELECT * FROM thread ORDER BY thread_id")).mappings().all()
            # Later additive migrations can add columns without changing any existing value.
            assert [{key: row[key] for key in before[0]} for row in after] == before
        assert not {"project_lead", "project_lead_worker"} & set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
