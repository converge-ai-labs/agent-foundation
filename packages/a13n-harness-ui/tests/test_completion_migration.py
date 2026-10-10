"""Completion metadata is additive and never fabricates historical successes."""

from a13n_harness_ui.storage.migration import DatabaseMigrator
from alembic import command
from sqlalchemy import create_engine, text


def test_completion_upgrade_preserves_legacy_writers_and_child_references(tmp_path):
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "122039abf689"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    insert = text(
        "INSERT INTO thread (thread_id, parent_thread_id, metadata_version, archived, created_at, updated_at, "
        "initial_state_schema_version, initial_state_digest) "
        "VALUES (:id, :parent, 1, 0, '2026-01-01', '2026-01-01', '1', :digest)"
    )
    try:
        with engine.begin() as connection:
            connection.execute(insert, {"id": "root", "parent": None, "digest": "1" * 64})
            connection.execute(insert, {"id": "child", "parent": "root", "digest": "2" * 64})
        with engine.connect() as legacy:
            migrator.upgrade()
            migrator.verify_current()
            assert legacy.execute(text("SELECT completion_version FROM thread")).scalars().all() == [0, 0]
            legacy.execute(insert, {"id": "legacy", "parent": None, "digest": "3" * 64})
            legacy.execute(
                text(
                    "UPDATE thread SET completion_version=1, completion_run_id='run-test', "
                    "completion_digest=:digest, completed_at='2026-01-02' WHERE thread_id='root'"
                ),
                {"digest": "a" * 64},
            )
            legacy.execute(text("UPDATE thread SET latest_reply='legacy reply' WHERE thread_id='root'"))
            legacy.commit()
            assert (
                legacy.execute(text("SELECT completion_version FROM thread WHERE thread_id='root'")).scalar_one() == 1
            )
            assert (
                legacy.execute(text("SELECT completion_version FROM thread WHERE thread_id='legacy'")).scalar_one() == 0
            )
            legacy.commit()
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT parent_thread_id FROM thread WHERE thread_id='child'")).scalar_one()
                == "root"
            )
            assert connection.execute(text("SELECT count(*) FROM thread")).scalar_one() == 3
        migrator.upgrade()
        migrator.verify_current()
    finally:
        engine.dispose()
