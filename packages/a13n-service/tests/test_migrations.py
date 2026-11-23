"""The generated revisions build exactly the schema the tests use: tables, rules and all."""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from a13n_service.distribution import OSS, Distribution
from a13n_service.migrations.runner import check, heads, migration_connection, upgrade
from a13n_service.settings import Database
from alembic import command
from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.exc import DBAPIError

from .conftest import pause_sweeps

RULES = """
SELECT 'function', p.proname, regexp_replace(pg_get_functiondef(p.oid), '\\s+', ' ', 'g')
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public'
UNION ALL
SELECT 'trigger', t.tgname || ' ON ' || t.tgrelid::regclass::text, pg_get_triggerdef(t.oid)
FROM pg_trigger t WHERE NOT t.tgisinternal
UNION ALL
SELECT 'check', c.conname || ' ON ' || c.conrelid::regclass::text,
       regexp_replace(pg_get_constraintdef(c.oid), ' NOT VALID$', '')
FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace WHERE n.nspname = 'public' AND c.contype = 'c'
ORDER BY 1, 2
"""

# Everything a revision can leave in the schema besides its CHECKs, which go with their tables.
OBJECTS = """
SELECT 'relation', c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
UNION ALL
SELECT 'function', p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public'
UNION ALL
SELECT 'trigger', t.tgname FROM pg_trigger t WHERE NOT t.tgisinternal
UNION ALL
SELECT 'type', t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
WHERE n.nspname = 'public' AND t.typtype IN ('e', 'd', 'r', 'm')
ORDER BY 1, 2
"""


def rows(database: Database, query: str) -> list[tuple[str, ...]]:
    engine = create_engine(database.url.get_secret_value())
    try:
        with engine.connect() as connection:
            return [tuple(row) for row in connection.execute(text(query))]
    finally:
        engine.dispose()


def test_migration_connection_assembles_schema_once(empty_database: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"metadata": 0, "rules": 0}
    metadata = Distribution.metadata
    table_rules = Distribution.table_rules

    def counted_metadata(distribution: Distribution) -> MetaData:
        calls["metadata"] += 1
        return metadata(distribution)

    def counted_rules(distribution: Distribution) -> dict[str, list[str]]:
        calls["rules"] += 1
        return table_rules(distribution)

    monkeypatch.setattr(Distribution, "metadata", counted_metadata)
    monkeypatch.setattr(Distribution, "table_rules", counted_rules)
    with migration_connection(empty_database, OSS) as config:
        assert config.attributes["connection"].scalar(text("SELECT 1")) == 1
        assert set(config.attributes["metadata"].tables) == {row.__table__.key for row in OSS.tables}
        assert config.attributes["rules"] == table_rules(OSS)
    assert calls == {"metadata": 1, "rules": 1}


def test_revisions_match_metadata_and_rules(empty_database: Database, database: Database) -> None:
    upgrade(empty_database, OSS)
    # Alembic compares tables, columns, indexes and keys; rules and CHECK expressions are compared directly.
    check(empty_database, OSS)
    migrated = rows(empty_database, RULES)
    assert migrated == rows(database, RULES)
    assert len(migrated) >= len(OSS.rules())


def test_oidc_upgrade_from_previous_head_preserves_password_identity(empty_database: Database) -> None:
    with migration_connection(empty_database, OSS) as config:
        command.upgrade(config, "ddc9c71ce4de")
    engine = create_engine(empty_database.url.get_secret_value())
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO principals (id, kind, name, email) "
                    "VALUES ('usr_existing', 'user', 'Existing', 'existing@example.test')"
                )
            )
        upgrade(empty_database, OSS)
        check(empty_database, OSS)
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT email, oidc_issuer, oidc_subject, oidc_email FROM principals WHERE id = 'usr_existing'")
            ).one() == ("existing@example.test", None, None, None)
            connection.execute(
                text(
                    "INSERT INTO principals (id, kind, name, oidc_issuer, oidc_subject, oidc_email) "
                    "VALUES ('usr_oidc', 'user', 'SSO', 'https://identity.example.test', 'subject', 'existing@example.test')"
                )
            )
        with (
            pytest.raises(DBAPIError, match="principal identity and home workspace are immutable"),
            engine.begin() as connection,
        ):
            connection.execute(text("UPDATE principals SET oidc_subject = 'another' WHERE id = 'usr_oidc'"))
    finally:
        engine.dispose()


def test_revisions_downgrade_to_an_empty_schema_and_upgrade_again(empty_database: Database) -> None:
    upgrade(empty_database, OSS)
    with migration_connection(empty_database, OSS) as config:
        command.downgrade(config, "base")
    # Only Alembic's own version table survives.
    assert rows(empty_database, OBJECTS) == [("relation", "alembic_version")]
    upgrade(empty_database, OSS)
    check(empty_database, OSS)


def test_run_usage_index_retries_after_invalid_concurrent_build(empty_database: Database) -> None:
    upgrade(empty_database, OSS)
    with migration_connection(empty_database, OSS) as config:
        command.downgrade(config, "48a66dd56558")
        # The build committed but the process stopped before stamping the revision.
        command.stamp(config, "a9a9c6399e27")
        connection = config.attributes["connection"]
        connection.execute(
            text("UPDATE pg_index SET indisvalid = false WHERE indexrelid = 'ix_runs_workspace_started'::regclass")
        )
        connection.commit()
    upgrade(empty_database, OSS)
    check(empty_database, OSS)
    assert rows(
        empty_database, "SELECT indisvalid::text FROM pg_index WHERE indexrelid = 'ix_runs_workspace_started'::regclass"
    ) == [("true",)]


def test_child_message_upgrade_retries_after_conflicting_lock(empty_database: Database) -> None:
    previous = "9d1cfd9c1a34"
    with migration_connection(empty_database, OSS) as config:
        command.upgrade(config, previous)
    before = rows(empty_database, RULES)
    engine = create_engine(empty_database.url.get_secret_value())
    try:
        with engine.begin() as writer:
            writer.execute(text("LOCK TABLE runs IN ROW EXCLUSIVE MODE"))
            with pytest.raises(DBAPIError) as busy:
                upgrade(empty_database, OSS)
            assert getattr(busy.value.orig, "sqlstate", None) == "55P03"
            assert rows(empty_database, "SELECT version_num FROM alembic_version") == [(previous,)]
            assert rows(empty_database, RULES) == before
    finally:
        engine.dispose()
    upgrade(empty_database, OSS)
    assert rows(empty_database, "SELECT version_num FROM alembic_version") == [(head,) for head in heads(OSS)]
    check(empty_database, OSS)


def test_run_usage_index_build_allows_run_writers(empty_database: Database) -> None:
    with migration_connection(empty_database, OSS) as config:
        command.upgrade(config, "a9a9c6399e27")
    engine = create_engine(empty_database.url.get_secret_value())
    try:
        with ThreadPoolExecutor(max_workers=1) as pool, engine.connect() as writer:
            transaction = writer.begin()
            writer.execute(text("LOCK TABLE runs IN ROW EXCLUSIVE MODE"))
            future = pool.submit(upgrade, empty_database, OSS)
            try:
                with engine.connect() as probe:
                    deadline = time.monotonic() + 10
                    while not probe.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity"
                            " WHERE datname = current_database() AND wait_event_type = 'Lock'"
                            " AND query LIKE 'CREATE INDEX CONCURRENTLY%ix_runs_workspace_started%')"
                        )
                    ):
                        probe.rollback()
                        if future.done():
                            future.result()
                            pytest.fail("Index creation did not wait for the existing writer")
                        assert time.monotonic() < deadline, "Index build did not start"
                        time.sleep(0.01)
                    # The build waits for the old writer, but permits a new write lock.
                    probe.execute(text("SET LOCAL lock_timeout = '1s'"))
                    probe.execute(text("LOCK TABLE runs IN ROW EXCLUSIVE MODE"))
                    probe.rollback()
            finally:
                transaction.rollback()
            future.result(timeout=10)
    finally:
        engine.dispose()
    check(empty_database, OSS)


@pytest.mark.anyio
async def test_session_binding_cutover_preserves_views_and_history(service, scripted_model, runs_kit):
    from .environments_support import reserve, with_fake_providers

    await pause_sweeps(service)
    env = await with_fake_providers(service)
    environment_id = (await reserve(env, env.template["id"]))["id"]
    agent = await runs_kit.create_agent(service, scripted_model)
    scripted_model.say("History survives the cutover.")
    submitted = await runs_kit.start_thread(
        service,
        agent,
        "Keep history",
        environments=[{"name": "workspace", "environment_id": environment_id}],
    )
    await (await runs_kit.attempt(service))
    initial = await runs_kit.get_run(service, submitted["run"]["id"])
    assert initial["status"] == "completed", str(initial)
    database = service.runtime.settings.database
    with migration_connection(database, OSS) as config:
        command.downgrade(config, "f6f3e4930b08")
        connection = config.attributes["connection"]
        connection.execute(
            text('UPDATE thread_execution_contexts SET sessions = \'{"old-view-hash": {"session_id": "old"}}\'::jsonb')
        )
        connection.commit()
    thread_id = submitted["thread"]["id"]
    history_query = f"SELECT message_history::text, version::text, updated_at::text FROM threads WHERE id='{thread_id}'"
    runs_query = f"SELECT (to_jsonb(runs) - 'app_activation')::text FROM runs WHERE thread_id='{thread_id}' ORDER BY id"
    mounts_query = "SELECT thread_id, environment_id, name, working_directory, permission_ceiling::text, created_at::text FROM thread_environments"
    before = [rows(database, query) for query in (history_query, runs_query, mounts_query)]
    upgrade(database, OSS)
    assert [rows(database, query) for query in (history_query, runs_query, mounts_query)] == before
    assert rows(database, "SELECT name, configuration::text, version::text, head FROM thread_session_bindings") == [
        ("default", "{}", "1", None)
    ]
    assert rows(database, "SELECT worker_id FROM thread_execution_contexts") == [(None,)]
    check(database, OSS)
    scripted_model.say("A fresh admission works.")
    response = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "Continue"))
    assert response.status_code == 201, response.text
    await (await runs_kit.attempt(service))
    assert (await runs_kit.get_run(service, response.json()["run"]["id"]))["status"] == "completed"
