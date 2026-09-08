from pathlib import Path

from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import SQLiteConfig
from a13n_service.storage.relational import sync_database_url
from sqlalchemy import create_engine, inspect


def test_gateway_binding_migration_round_trip(tmp_path: Path) -> None:
    config = SQLiteConfig(path=tmp_path / "gateway-migrations.sqlite3")
    migrator = DatabaseMigrator(config)

    migrator.upgrade()
    engine = create_engine(sync_database_url(config))
    try:
        inspector = inspect(engine)
        assert {"agui_thread_bindings", "agui_run_bindings"} <= set(inspector.get_table_names())
        thread_indexes = {item["name"] for item in inspector.get_indexes("agui_thread_bindings")}
        run_indexes = {item["name"] for item in inspector.get_indexes("agui_run_bindings")}
        assert "uq_agui_thread_bindings_id_scope" in thread_indexes
        assert "uq_agui_run_bindings_id_scope" in run_indexes
    finally:
        engine.dispose()

    migrator.downgrade("base")
    engine = create_engine(sync_database_url(config))
    try:
        assert {"agui_thread_bindings", "agui_run_bindings"}.isdisjoint(inspect(engine).get_table_names())
    finally:
        engine.dispose()
