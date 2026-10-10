"""Real pre-touch SQLite writers coexist with the additive navigation migration."""

from a13n_harness_ui.storage.migration import DatabaseMigrator
from alembic import command
from sqlalchemy import create_engine, inspect, text


def test_navigation_migration_preserves_order_old_writers_and_incoming_references(tmp_path, before_comment_retirement):
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "20e4b84abfd1"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    insert = text(
        "INSERT INTO thread (thread_id, parent_thread_id, metadata_version, archived, created_at, updated_at, "
        "initial_state_schema_version, initial_state_digest) "
        "VALUES (:id, :parent, 1, 0, '2026-01-01 00:00:00.000000', :updated, '1', :digest)"
    )
    try:
        with engine.begin() as connection:
            connection.execute(
                insert, {"id": "root", "parent": None, "updated": "2026-01-03 00:00:00.000000", "digest": "1" * 64}
            )
            connection.execute(
                insert, {"id": "child", "parent": "root", "updated": "2026-01-02 00:00:00.000000", "digest": "2" * 64}
            )
        # Keep the old writer connection alive while the newer package upgrades.
        with engine.connect() as legacy:
            migrator.upgrade()
            migrator.verify_current()
            assert legacy.execute(text("SELECT thread_id FROM thread ORDER BY touched_at DESC")).scalars().all() == [
                "root",
                "child",
            ]
            before = legacy.execute(text("SELECT touched_at FROM thread WHERE thread_id = 'root'")).scalar_one()
            legacy.execute(text("UPDATE thread SET updated_at = '2026-02-01 00:00:00.000000' WHERE thread_id = 'root'"))
            legacy.execute(
                insert,
                {"id": "older-writer", "parent": None, "updated": "2026-02-02 00:00:00.000000", "digest": "3" * 64},
            )
            legacy.commit()
            assert legacy.execute(text("SELECT touched_at FROM thread WHERE thread_id = 'root'")).scalar_one() == before
            assert (
                legacy.execute(text("SELECT touched_at FROM thread WHERE thread_id = 'older-writer'")).scalar_one()
                is None
            )
            assert (
                legacy.execute(
                    text(
                        "SELECT coalesce(touched_at, created_at) = created_at FROM thread WHERE thread_id = 'older-writer'"
                    )
                ).scalar_one()
                == 1
            )
            legacy.commit()
            assert legacy.execute(text("SELECT starred FROM thread")).scalars().all() == [False, False, False]
        assert "ix_thread_touched_at" in {item["name"] for item in inspect(engine).get_indexes("thread")}
        migrator._run(lambda config: command.downgrade(config, "20e4b84abfd1"), write=True)
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT parent_thread_id, initial_state_digest FROM thread WHERE thread_id = 'child'")
            ).one() == ("root", "2" * 64)
            assert connection.execute(text("SELECT count(*) FROM thread")).scalar_one() == 3
        assert "touched_at" not in {item["name"] for item in inspect(engine).get_columns("thread")}
        migrator.upgrade()
        migrator.verify_current()
    finally:
        engine.dispose()
