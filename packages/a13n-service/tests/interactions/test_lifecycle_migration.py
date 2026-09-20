import pytest
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.relational import database_url
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError


def test_lifecycle_fact_trigger_is_installed(service_database: PostgreSQLConfig) -> None:
    engine = create_engine(database_url(service_database))
    try:
        with engine.connect() as connection:
            triggers = set(
                connection.execute(
                    text(
                        "SELECT trigger_name FROM information_schema.triggers "
                        "WHERE event_object_table = 'lifecycle_events'"
                    )
                ).scalars()
            )
    finally:
        engine.dispose()
    assert "reject_lifecycle_fact_update" in triggers


def test_fact_guard_allows_projection_but_preserves_json(service_database: PostgreSQLConfig) -> None:
    engine = create_engine(database_url(service_database))
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
