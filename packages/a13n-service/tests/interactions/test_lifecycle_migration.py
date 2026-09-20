from importlib import import_module

import pytest
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.relational import database_url
from alembic.migration import MigrationContext
from alembic.operations import Operations
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


def test_counter_backfill_preserves_retained_watermarks_and_pruned_history(service_database):
    migration = import_module(
        "a13n_service.database.migrations.versions.20260920_ec56e4426fb7_add_resource_lifecycle_counters"
    )
    engine = create_engine(database_url(service_database))
    try:
        with engine.begin() as connection:
            for table in ("runs", "run_attempts"):
                connection.execute(
                    text(
                        f"CREATE TEMP TABLE {table} AS SELECT id, organization_id, version FROM public.{table} WITH NO DATA"
                    )
                )
                connection.execute(
                    text(
                        f"INSERT INTO {table} (id, organization_id, version) VALUES "
                        "('retained', 'org', 12), ('pruned', 'org', 9), ('other', 'org-other', 7)"
                    )
                )
            connection.execute(
                text(
                    "CREATE TEMP TABLE lifecycle_events AS SELECT organization_id, entity_type, entity_id, resource_seq "
                    "FROM public.lifecycle_events WITH NO DATA"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO lifecycle_events VALUES "
                    "('org', 'run', 'retained', 3), ('org', 'run', 'retained', 4), "
                    "('org', 'run_attempt', 'retained', 2), ('another-org', 'run', 'retained', 90)"
                )
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            for table, expected in (("runs", 4), ("run_attempts", 2)):
                rows = dict(connection.execute(text(f"SELECT id, lifecycle_seq FROM {table}")).all())
                assert rows == {"retained": expected, "pruned": 9, "other": 7}
                connection.execute(text(f"INSERT INTO {table} (id, organization_id, version) VALUES ('new', 'org', 1)"))
                assert connection.scalar(text(f"SELECT lifecycle_seq FROM {table} WHERE id = 'new'")) == 0
    finally:
        engine.dispose()


def test_terminal_guards_allow_only_monotonic_counter_changes(service_database):
    engine = create_engine(database_url(service_database))
    try:
        with engine.begin() as connection:
            for table, state in (("runs", "failed"), ("run_attempts", "cancelled")):
                connection.execute(text(f"CREATE TEMP TABLE {table} AS SELECT * FROM public.{table} WITH NO DATA"))
                connection.execute(
                    text(
                        f"CREATE TRIGGER counter_terminal_guard BEFORE UPDATE ON {table} "
                        "FOR EACH ROW EXECUTE FUNCTION reject_terminal_interaction_update()"
                    )
                )
                connection.execute(
                    text(f"INSERT INTO {table} (status, version, lifecycle_seq) VALUES (:status, 3, 2)"),
                    {"status": state},
                )
                connection.execute(text(f"UPDATE {table} SET lifecycle_seq = 3"))
                for mutation, message in (("version = 4", "immutable"), ("lifecycle_seq = 1", "cannot decrease")):
                    with pytest.raises(DBAPIError, match=message), connection.begin_nested():
                        connection.execute(text(f"UPDATE {table} SET {mutation}"))
                assert connection.scalar(text(f"SELECT lifecycle_seq FROM {table}")) == 3
    finally:
        engine.dispose()
