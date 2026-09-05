from pathlib import Path

from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import SQLiteConfig
from a13n_service.storage.relational import sync_database_url
from sqlalchemy import create_engine, inspect

SKILL_TABLES = {
    "idempotency_evidence",
    "skill_uploads",
    "skill_revisions",
    "skills",
}


def test_skill_schema_migrates_up_and_down_on_sqlite(tmp_path: Path) -> None:
    config = SQLiteConfig(path=tmp_path / "migrations.sqlite3")
    migrator = DatabaseMigrator(config)

    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    engine = create_engine(sync_database_url(config))
    try:
        inspector = inspect(engine)
        assert SKILL_TABLES <= set(inspector.get_table_names())
        skill_columns = {column["name"] for column in inspector.get_columns("skills")}
        assert "key" in skill_columns
        assert "display_name" not in skill_columns
        assert any(
            index["name"] == "uq_skills_workspace_key_active" and index["unique"]
            for index in inspector.get_indexes("skills")
        )
    finally:
        engine.dispose()

    migrator.downgrade("base")
    engine = create_engine(sync_database_url(config))
    try:
        assert SKILL_TABLES.isdisjoint(inspect(engine).get_table_names())
    finally:
        engine.dispose()
