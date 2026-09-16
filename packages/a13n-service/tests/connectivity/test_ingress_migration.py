from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.relational import database_url
from sqlalchemy import create_engine, inspect

TABLES = {
    "application_accounts",
    "agent_thread_bindings",
    "idempotency_evidence",
    "ingress_admissions",
    "ingress_batches",
    "account_targets",
}


def _exercise(config: PostgreSQLConfig) -> None:
    migrator = DatabaseMigrator(config)
    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    engine = create_engine(database_url(config))
    try:
        inspector = inspect(engine)
        assert TABLES <= set(inspector.get_table_names())
        run_columns = {column["name"] for column in inspector.get_columns("runs")}
        assert "native_tool_contexts_json" in run_columns
        assert not {"account_selections_json", "ingress_context_json"} & run_columns
        assert {"ingresses", "ingress_routes", "ingress_agents", "ingress_batch_events"}.isdisjoint(
            inspector.get_table_names()
        )
        account_columns = {column["name"] for column in inspector.get_columns("application_accounts")}
        assert {
            "ciphertext",
            "nonce",
            "encryption_key_id",
            "credential_generation",
            "identity_digest",
        } <= account_columns
        command_columns = {column["name"] for column in inspector.get_columns("idempotency_evidence")}
        assert "key_digest" in command_columns
        assert "idempotency_key" not in command_columns
        admission_columns = {column["name"] for column in inspector.get_columns("ingress_admissions")}
        assert {"batch_id", "event_json", "dedup_expires_at"} <= admission_columns
        assert {"raw_ref_json", "mapping_digest", "status", "available_at"}.isdisjoint(admission_columns)
    finally:
        engine.dispose()
    migrator.downgrade("base")
    engine = create_engine(database_url(config))
    try:
        assert TABLES.isdisjoint(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_ingress_schema_migrates(postgres_database: PostgreSQLConfig) -> None:
    _exercise(postgres_database)
