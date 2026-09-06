from pathlib import Path

import pytest
from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.relational import sync_database_url
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError


def _assert_lifecycle_schema(config: PostgreSQLConfig | SQLiteConfig, *, present: bool) -> None:
    engine = create_engine(sync_database_url(config))
    try:
        inspector = inspect(engine)
        if not present:
            assert "lifecycle_events" not in inspector.get_table_names()
            return
        assert "lifecycle_events" in inspector.get_table_names()
        indexes = {index["name"] for index in inspector.get_indexes("lifecycle_events")}
        assert {
            "ix_lifecycle_events_attempt",
            "ix_lifecycle_events_projection_due",
            "ix_lifecycle_events_resource",
            "ix_lifecycle_events_run",
            "ix_lifecycle_events_organization_seq",
        } <= indexes
        checks = {constraint["name"] for constraint in inspector.get_check_constraints("lifecycle_events")}
        assert {
            "ck_lifecycle_events_entity_correlation_valid",
            "ck_lifecycle_events_event_type_valid",
            "ck_lifecycle_events_projection_shape_valid",
            "ck_lifecycle_events_schema_version_non_blank",
        } <= checks
        with engine.connect() as connection:
            if connection.dialect.name == "postgresql":
                triggers = set(
                    connection.execute(
                        text(
                            "SELECT trigger_name FROM information_schema.triggers "
                            "WHERE event_object_table = 'lifecycle_events'"
                        )
                    ).scalars()
                )
            else:
                triggers = set(
                    connection.execute(
                        text("SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'lifecycle_events'")
                    ).scalars()
                )
        assert "reject_lifecycle_fact_update" in triggers
    finally:
        engine.dispose()


def _exercise_migration(config: PostgreSQLConfig | SQLiteConfig) -> None:
    migrator = DatabaseMigrator(config)
    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    _assert_lifecycle_schema(config, present=True)
    migrator.downgrade("base")
    _assert_lifecycle_schema(config, present=False)


def test_lifecycle_schema_migrates_up_and_down_on_sqlite(tmp_path: Path) -> None:
    _exercise_migration(SQLiteConfig(path=tmp_path / "lifecycle-migrations.sqlite3"))


def test_lifecycle_schema_migrates_up_and_down_on_postgresql(pg_url: str) -> None:
    _exercise_migration(PostgreSQLConfig(url=pg_url))


def test_postgresql_fact_guard_allows_projection_but_preserves_json(pg_url: str) -> None:
    config = PostgreSQLConfig(url=pg_url)
    migrator = DatabaseMigrator(config)
    migrator.upgrade()
    engine = create_engine(sync_database_url(config))
    try:
        with engine.begin() as connection:
            # Exercise the installed function with the real column types while
            # keeping this migration test independent of Run creation fixtures.
            connection.execute(
                text("CREATE TEMP TABLE lifecycle_guard_probe AS SELECT * FROM lifecycle_events WITH NO DATA")
            )
            connection.execute(
                text(
                    "CREATE TRIGGER lifecycle_guard_probe BEFORE UPDATE ON lifecycle_guard_probe "
                    "FOR EACH ROW EXECUTE FUNCTION reject_lifecycle_fact_update()"
                )
            )
            connection.execute(
                text("INSERT INTO lifecycle_guard_probe (payload, projection_attempts) VALUES ('{\"key\": 1}', 0)")
            )
            connection.execute(text("UPDATE lifecycle_guard_probe SET projection_attempts = 1"))
            connection.execute(text("UPDATE lifecycle_guard_probe SET payload = payload"))
            assert connection.scalar(text("SELECT projection_attempts FROM lifecycle_guard_probe")) == 1
            for mutation in (
                "payload = '{\"key\": 2}'",
                "payload = '{\"key\":1}'",
                "actor_id = 'actor-changed'",
            ):
                with pytest.raises(DBAPIError, match="lifecycle fact columns are immutable"), connection.begin_nested():
                    connection.exec_driver_sql(f"UPDATE lifecycle_guard_probe SET {mutation}")
            assert connection.scalar(text("SELECT payload::text FROM lifecycle_guard_probe")) == '{"key": 1}'
    finally:
        engine.dispose()
        migrator.downgrade("base")
