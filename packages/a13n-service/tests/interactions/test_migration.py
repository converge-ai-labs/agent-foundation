from a13n_service.database.metadata import service_metadata
from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.relational import database_url
from sqlalchemy import create_engine, inspect, text

INTERACTION_TABLES = {
    "sessions",
    "threads",
    "runs",
    "run_attempts",
    "thread_inbox",
    "thread_queued_submissions",
    "child_run_relationships",
}


def _assert_schema(config: PostgreSQLConfig, *, present: bool) -> None:
    engine = create_engine(database_url(config))
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        if not present:
            assert INTERACTION_TABLES.isdisjoint(tables)
            return
        assert INTERACTION_TABLES <= tables
        for table in INTERACTION_TABLES:
            columns = {column["name"] for column in inspector.get_columns(table)}
            assert columns == set(service_metadata().tables[table].columns.keys()), table
        run_columns = {column["name"] for column in inspector.get_columns("runs")}
        assert {
            "connection_selections_json",
            "current_run_attempt_id",
            "execution_policy_version",
            "sealed_state_digest_sha256",
        } <= run_columns
        assert "connector_selections_json" not in run_columns
        assert "mcp_selections_json" not in run_columns
        thread_columns = {column["name"] for column in inspector.get_columns("threads")}
        assert {"next_delivery_sequence", "pending_count", "pending_bytes"} <= thread_columns
        run_indexes = {index["name"] for index in inspector.get_indexes("runs")}
        assert {
            "ix_runs_worker_scan",
            "uq_runs_active_thread",
            "uq_runs_idempotency",
            "uq_runs_thread_authority",
        } <= run_indexes
        run_unique = {constraint["name"] for constraint in inspector.get_unique_constraints("runs")}
        assert {
            "uq_runs_organization_id",
            "uq_runs_organization_thread_id",
            "uq_runs_scope_identity",
        } <= run_unique
        run_checks = {constraint["name"] for constraint in inspector.get_check_constraints("runs")}
        assert {
            "ck_runs_input_representation_valid",
            "ck_runs_current_attempt_lifecycle_valid",
            "ck_runs_sealed_state_lifecycle_valid",
        } <= run_checks
        attempt_indexes = {index["name"] for index in inspector.get_indexes("run_attempts")}
        assert {
            "ix_run_attempts_live_lease",
            "uq_run_attempts_number",
            "uq_run_attempts_fence_identity",
        } <= attempt_indexes
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
            "organization_id",
            "origin_thread_id",
            "origin_run_id",
        )
        run_foreign_keys = {constraint["name"] for constraint in inspector.get_foreign_keys("runs")}
        assert {"fk_runs_current_attempt_same_run", "fk_runs_sealed_attempt_same_run"} <= run_foreign_keys
        thread_columns = {column["name"] for column in inspector.get_columns("threads")}
        assert {"queue_version", "next_delivery_sequence", "pending_count", "pending_bytes"} <= thread_columns
        thread_checks = {constraint["name"] for constraint in inspector.get_check_constraints("threads")}
        assert {
            "ck_threads_next_delivery_sequence_positive",
            "ck_threads_pending_count_non_negative",
            "ck_threads_pending_bytes_non_negative",
        } <= thread_checks
        inbox_indexes = {index["name"] for index in inspector.get_indexes("thread_inbox")}
        assert {
            "ix_thread_inbox_fifo",
            "ix_thread_inbox_target",
            "ix_thread_inbox_waiting_source",
            "ix_thread_inbox_kind_scan",
            "ix_thread_inbox_origin",
        } <= inbox_indexes
        inbox_checks = {constraint["name"] for constraint in inspector.get_check_constraints("thread_inbox")}
        assert {
            "ck_thread_inbox_payload_valid",
            "ck_thread_inbox_kind_provenance_valid",
            "ck_thread_inbox_status_evidence_valid",
            "ck_thread_inbox_pending_binding_valid",
            "ck_thread_inbox_target_waiting_source_distinct",
        } <= inbox_checks
        inbox_foreign_keys = {constraint["name"] for constraint in inspector.get_foreign_keys("thread_inbox")}
        assert "fk_thread_inbox_async_subagent_relationship" in inbox_foreign_keys
        inbox_unique = {constraint["name"] for constraint in inspector.get_unique_constraints("thread_inbox")}
        assert "uq_thread_inbox_async_subagent_relationship" in inbox_unique
        queue_indexes = {index["name"] for index in inspector.get_indexes("thread_queued_submissions")}
        assert {
            "uq_thread_queued_submissions_position",
            "ix_thread_queued_submissions_live",
            "ix_thread_queued_submissions_consumed",
        } <= queue_indexes
        queue_foreign_keys = {
            constraint["name"] for constraint in inspector.get_foreign_keys("thread_queued_submissions")
        }
        assert "fk_queued_submissions_consumed_run_authority" in queue_foreign_keys
        child_relationship_foreign_keys = {
            constraint["name"] for constraint in inspector.get_foreign_keys("child_run_relationships")
        }
        assert {
            "fk_child_run_relationships_parent_run",
            "fk_child_run_relationships_parent_attempt",
            "fk_child_run_relationships_child_thread",
            "fk_child_run_relationships_child_run",
        } <= child_relationship_foreign_keys
        child_relationship_unique = {
            constraint["name"] for constraint in inspector.get_unique_constraints("child_run_relationships")
        }
        child_relationship_columns = {column["name"] for column in inspector.get_columns("child_run_relationships")}
        assert "spawn_operation_id" not in child_relationship_columns
        assert "uq_child_run_relationships_spawn_operation" not in child_relationship_unique
        assert {
            "uq_child_run_relationships_organization_id",
            "uq_child_run_relationships_child_run",
        } <= child_relationship_unique
        with engine.connect() as connection:
            trigger_names = set(
                connection.execute(
                    text(
                        "SELECT trigger_name FROM information_schema.triggers "
                        "WHERE event_object_table IN ('runs', 'run_attempts')"
                    )
                ).scalars()
            )
        assert {"reject_sealed_run_update", "reject_terminal_run_attempt_update"} <= trigger_names
    finally:
        engine.dispose()


def _exercise_migration(config: PostgreSQLConfig) -> None:
    migrator = DatabaseMigrator(config)
    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    _assert_schema(config, present=True)
    migrator.downgrade("base")
    _assert_schema(config, present=False)


def test_interaction_schema_migrates_up_and_down(postgres_database: PostgreSQLConfig) -> None:
    _exercise_migration(postgres_database)
