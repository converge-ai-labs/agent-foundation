import pytest
from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.relational import database_url
from alembic import op
from sqlalchemy import create_engine, inspect, text

PREVIOUS_REVISION = "ec86f1da8c98"
ACCOUNTING_REVISION = "56b4ee866468"


def test_accounting_migration_adds_and_removes_counter_columns(postgres_database: PostgreSQLConfig):
    migrator = DatabaseMigrator(postgres_database)
    migrator.upgrade(ACCOUNTING_REVISION)
    engine = create_engine(database_url(postgres_database))
    try:
        inspector = inspect(engine)
        assert {"next_delivery_sequence", "pending_count", "pending_bytes"} <= {
            column["name"] for column in inspector.get_columns("threads")
        }
        assert "thread_inbox_counters" not in inspector.get_table_names()
        migrator.downgrade(PREVIOUS_REVISION)
        inspector = inspect(engine)
        assert "thread_inbox_counters" in inspector.get_table_names()
        assert {"next_delivery_sequence", "pending_count", "pending_bytes"}.isdisjoint(
            column["name"] for column in inspector.get_columns("threads")
        )
    finally:
        engine.dispose()


def test_interrupted_accounting_migration_rolls_back_and_can_retry(postgres_database: PostgreSQLConfig, monkeypatch):
    migrator = DatabaseMigrator(postgres_database)
    migrator.upgrade(PREVIOUS_REVISION)
    drop_table = op.drop_table

    def fail_before_drop(name, *args, **kwargs):
        if name == "thread_inbox_counters":
            raise RuntimeError("interrupted before counter drop")
        return drop_table(name, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(op, "drop_table", fail_before_drop)
        with pytest.raises(RuntimeError, match="interrupted before counter drop"):
            migrator.upgrade(ACCOUNTING_REVISION)
    engine = create_engine(database_url(postgres_database))
    try:
        assert "thread_inbox_counters" in inspect(engine).get_table_names()
        assert "next_delivery_sequence" not in {column["name"] for column in inspect(engine).get_columns("threads")}
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS_REVISION
        migrator.upgrade(ACCOUNTING_REVISION)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == ACCOUNTING_REVISION
    finally:
        engine.dispose()
