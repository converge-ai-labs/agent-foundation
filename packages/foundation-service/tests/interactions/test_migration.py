from pathlib import Path

from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.relational import sync_database_url
from sqlalchemy import create_engine, inspect

INTERACTION_TABLES = {"sessions", "threads", "runs", "run_attempts"}


def _assert_schema(config: PostgreSQLConfig | SQLiteConfig, *, present: bool) -> None:
    engine = create_engine(sync_database_url(config))
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        if not present:
            assert INTERACTION_TABLES.isdisjoint(tables)
            return
        assert INTERACTION_TABLES <= tables
        run_columns = {column["name"] for column in inspector.get_columns("runs")}
        assert {
            "current_run_attempt_id",
            "recovery_policy_version",
            "sealed_state_digest_sha256",
        } <= run_columns
        run_indexes = {index["name"] for index in inspector.get_indexes("runs")}
        assert {"ix_runs_worker_scan", "uq_runs_active_thread", "uq_runs_idempotency"} <= run_indexes
        run_unique = {constraint["name"] for constraint in inspector.get_unique_constraints("runs")}
        assert {"uq_runs_tenant_id", "uq_runs_tenant_thread_id", "uq_runs_scope_identity"} <= run_unique
        run_checks = {constraint["name"] for constraint in inspector.get_check_constraints("runs")}
        assert {
            "ck_runs_input_representation_valid",
            "ck_runs_current_attempt_lifecycle_valid",
            "ck_runs_sealed_state_lifecycle_valid",
        } <= run_checks
        attempt_indexes = {index["name"] for index in inspector.get_indexes("run_attempts")}
        assert {"ix_run_attempts_live_lease", "uq_run_attempts_fence", "uq_run_attempts_number"} <= attempt_indexes
        attempt_checks = {constraint["name"] for constraint in inspector.get_check_constraints("run_attempts")}
        assert {
            "ck_run_attempts_harness_lifecycle_valid",
            "ck_run_attempts_finished_at_lifecycle_valid",
        } <= attempt_checks
        thread_foreign_keys = {
            constraint["name"]: tuple(constraint["constrained_columns"])
            for constraint in inspector.get_foreign_keys("threads")
        }
        assert {
            "fk_threads_origin_run_same_thread",
            "fk_threads_head_run_same_thread",
            "fk_threads_current_run_same_thread",
        } <= thread_foreign_keys.keys()
        assert thread_foreign_keys["fk_threads_origin_run_same_thread"] == (
            "tenant_id",
            "origin_thread_id",
            "origin_run_id",
        )
        run_foreign_keys = {constraint["name"] for constraint in inspector.get_foreign_keys("runs")}
        assert {"fk_runs_current_attempt_same_run", "fk_runs_sealed_attempt_same_run"} <= run_foreign_keys
    finally:
        engine.dispose()


def _exercise_migration(config: PostgreSQLConfig | SQLiteConfig) -> None:
    migrator = DatabaseMigrator(config)
    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    _assert_schema(config, present=True)
    migrator.downgrade("base")
    _assert_schema(config, present=False)


def test_interaction_schema_migrates_up_and_down_on_sqlite(tmp_path: Path) -> None:
    _exercise_migration(SQLiteConfig(path=tmp_path / "interaction-migrations.sqlite3"))


def test_interaction_schema_migrates_up_and_down_on_postgresql(pg_url: str) -> None:
    _exercise_migration(PostgreSQLConfig(url=pg_url))
