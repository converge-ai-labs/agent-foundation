"""Database triggers are outside declared metadata, so the migrated schema is checked for them here."""

from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.relational import database_url
from sqlalchemy import create_engine, text


def test_sealed_run_and_terminal_attempt_triggers_are_installed(service_database: PostgreSQLConfig) -> None:
    engine = create_engine(database_url(service_database))
    try:
        with engine.connect() as connection:
            trigger_names = set(
                connection.execute(
                    text(
                        "SELECT trigger_name FROM information_schema.triggers "
                        "WHERE event_object_table IN ('runs', 'run_attempts')"
                    )
                ).scalars()
            )
    finally:
        engine.dispose()
    assert {"reject_sealed_run_update", "reject_terminal_run_attempt_update"} <= trigger_names
