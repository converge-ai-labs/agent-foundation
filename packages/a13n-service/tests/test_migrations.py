"""The generated revisions build exactly the schema the tests use: tables, rules and all."""

import json

import pytest
from a13n_service.distribution import OSS
from a13n_service.infra.db import transaction
from a13n_service.infra.platform import PLATFORM_GROUPS, PLATFORM_TOOLS
from a13n_service.migrations.runner import check, migration_connection, upgrade
from a13n_service.resources.revisions import config_digest
from a13n_service.runs.tables import SessionRow
from a13n_service.runs.threads import new_thread
from a13n_service.settings import Database
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

RULES = """
SELECT 'function', p.proname, regexp_replace(pg_get_functiondef(p.oid), '\\s+', ' ', 'g')
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public'
UNION ALL
SELECT 'trigger', t.tgname || ' ON ' || t.tgrelid::regclass::text, pg_get_triggerdef(t.oid)
FROM pg_trigger t WHERE NOT t.tgisinternal
UNION ALL
SELECT 'check', c.conname || ' ON ' || c.conrelid::regclass::text, pg_get_constraintdef(c.oid)
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


def test_revisions_match_metadata_and_rules(empty_database: Database, database: Database) -> None:
    upgrade(empty_database, OSS)
    # Alembic compares tables, columns, indexes and keys; rules and CHECK expressions are compared directly.
    check(empty_database, OSS)
    migrated = rows(empty_database, RULES)
    assert migrated == rows(database, RULES)
    assert len(migrated) >= len(OSS.rules())


def test_schedule_revision_upgrades_and_restores_main_schema(empty_database: Database) -> None:
    with migration_connection(empty_database, OSS) as config:
        command.upgrade(config, "fed344f2d755")
    original_rules = rows(empty_database, RULES)
    original_objects = rows(empty_database, OBJECTS)
    upgrade(empty_database, OSS)
    check(empty_database, OSS)
    assert len(rows(empty_database, "SELECT version_num FROM alembic_version")) == 1
    assert rows(
        empty_database,
        "SELECT is_nullable FROM information_schema.columns WHERE table_schema = 'public'"
        " AND table_name = 'schedule_occurrences' AND column_name = 'next_attempt_at'",
    ) == [("YES",)]
    with migration_connection(empty_database, OSS) as config:
        command.downgrade(config, "fed344f2d755")
    assert rows(empty_database, RULES) == original_rules
    assert rows(empty_database, OBJECTS) == original_objects


def test_revisions_downgrade_to_an_empty_schema_and_upgrade_again(empty_database: Database) -> None:
    upgrade(empty_database, OSS)
    with migration_connection(empty_database, OSS) as config:
        command.downgrade(config, "base")
    # Only Alembic's own version table survives.
    assert rows(empty_database, OBJECTS) == [("relation", "alembic_version")]
    upgrade(empty_database, OSS)
    check(empty_database, OSS)


@pytest.mark.parametrize("invalid_index", [False, True])
def test_usage_cursor_migration_retries_after_concurrent_index_build(
    empty_database: Database, invalid_index: bool
) -> None:
    upgrade(empty_database, OSS)
    with migration_connection(empty_database, OSS) as config:
        # Concurrent index DDL commits before the revision marker. Model an
        # interruption in that window, including a failed build's invalid index.
        # Remove later revisions first; this scenario concerns only replay of the index revision.
        command.downgrade(config, "944ba3e51ffd")
        command.stamp(config, "123fec952fe1")
        if invalid_index:
            connection = config.attributes["connection"]
            connection.execute(
                text(
                    "UPDATE pg_index SET indisvalid = false WHERE indexrelid = 'ix_usage_records_scope_owner'::regclass"
                )
            )
            connection.commit()
    upgrade(empty_database, OSS)
    check(empty_database, OSS)
    assert rows(
        empty_database,
        "SELECT indisvalid::text FROM pg_index WHERE indexrelid = 'ix_usage_records_scope_owner'::regclass",
    ) == [("true",)]


@pytest.mark.anyio
async def test_history_migration_preserves_existing_rows_and_guards_order(database, runtime, tenant):  # type: ignore[no-untyped-def]
    from .test_runs_input_context import HISTORY_INPUT

    async with transaction(runtime.storage) as session:
        parent = SessionRow(
            id="session_migration",
            organization_id=tenant.organization_id,
            workspace_id=tenant.workspace_id,
            created_by_id=tenant.principal_id,
            labels={},
        )
        session.add(parent)
        await session.flush()
        thread = new_thread(parent, mcp_headers={}, message_history=HISTORY_INPUT)
        session.add(thread)
        await session.flush()
        thread_id = thread.id

    query = f"SELECT message_history::text FROM threads WHERE id = '{thread_id}'"
    ordered = rows(database, query)
    with migration_connection(database, OSS) as config:
        command.downgrade(config, "ce6627932e2f")
    normalized = rows(database, query)
    assert normalized != ordered
    assert json.loads(normalized[0][0]) == HISTORY_INPUT
    upgrade(database, OSS)
    assert rows(database, query) == normalized
    check(database, OSS)

    engine = create_engine(database.url.get_secret_value())
    try:
        # A non-history update still works after the JSON transition.
        with engine.begin() as connection:
            connection.execute(text("UPDATE threads SET labels = '{}'::jsonb WHERE id = :id"), {"id": thread_id})
        with pytest.raises(DBAPIError, match="initial history is immutable"):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE threads SET message_history = CAST(:history AS json) WHERE id = :id"),
                    {"id": thread_id, "history": json.dumps(HISTORY_INPUT)},
                )
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_legacy_toolsets_and_platform_selections_are_rewritten(database, service, runs_kit, scripted_model):  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    overridden = {"overrides": {"toolsets": {"web": {"enabled": False}}}}
    run = (await runs_kit.start_thread(service, agent, "hi", options=overridden))["run"]
    legacy = json.dumps({key: {"enabled": True} for key in ("configuration", "traces", "findings")})
    groups = "'{" + ",".join(PLATFORM_GROUPS) + "}'::text[]"
    # The revision selects two groups and asks before reads; the run selects one group and allows writes.
    selection = json.dumps({"groups": ["agents", "execution"], "read_permission": "ask", "write_permission": "ask"})
    override = json.dumps({"groups": ["execution"], "read_permission": "allow", "write_permission": "allow"})
    engine = create_engine(database.url.get_secret_value())
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL session_replication_role = replica"))
            connection.execute(
                text(
                    "UPDATE agent_revisions SET config = jsonb_set(config, '{toolsets}',"
                    f" ((config->'toolsets') - {groups}) || CAST(:legacy AS jsonb))"
                    " || jsonb_build_object('platform_tools', CAST(:selection AS jsonb))"
                ),
                {"legacy": legacy, "selection": selection},
            )
            for table in ("runs", "inbox_entries"):
                connection.execute(
                    text(
                        f"UPDATE {table} SET options = jsonb_set(jsonb_set(options, '{{overrides,toolsets}}',"
                        " options #> '{overrides,toolsets}' || CAST(:legacy AS jsonb)),"
                        " '{overrides,platform_tools}', CAST(:override AS jsonb))"
                    ),
                    {"legacy": legacy, "override": override},
                )
    finally:
        engine.dispose()
    with migration_connection(database, OSS) as config:
        command.downgrade(config, "4d51a94777d9")
    upgrade(database, OSS)

    named = "?| '{configuration,traces,findings}'"
    assert rows(
        database,
        f"SELECT count(*) FROM agent_revisions WHERE config->'toolsets' {named}"
        f" UNION ALL SELECT count(*) FROM runs WHERE options #> '{{overrides,toolsets}}' {named}"
        f" UNION ALL SELECT count(*) FROM inbox_entries WHERE options #> '{{overrides,toolsets}}' {named}",
    ) == [(0,), (0,), (0,)]
    assert rows(
        database,
        "SELECT count(*) FROM agent_revisions WHERE config ? 'platform_tools'"
        " UNION ALL SELECT count(*) FROM runs WHERE options->'overrides' ? 'platform_tools'"
        " UNION ALL SELECT count(*) FROM inbox_entries WHERE options->'overrides' ? 'platform_tools'",
    ) == [(0,), (0,), (0,)]
    assert all(
        digest == config_digest(stored)
        for stored, digest in rows(database, "SELECT config, digest FROM agent_revisions")
    )
    # The guards are back on, and the rewritten rows read as current configurations.
    assert rows(database, "SELECT DISTINCT tgenabled FROM pg_trigger WHERE NOT tgisinternal") == [("O",)]
    effects = {tool.key: tool.effect for tool in PLATFORM_TOOLS}

    def permissions(toolset: dict) -> set[tuple[str, str]]:
        return {(effects[key], tool["permission"]) for key, tool in toolset["tools"].items() if tool["enabled"]}

    revision = await service.client.get(f"{service.api}/agents/{agent['id']}/revisions/{agent['default_revision_id']}")
    stored = revision.json()["config"]["toolsets"]
    assert [group for group in PLATFORM_GROUPS if stored[group]["enabled"]] == ["agents", "execution"]
    assert permissions(stored["agents"]) == {("read", "ask"), ("write", "inherit")}
    executed = await service.client.get(f"{service.api}/runs/{run['id']}/agent")
    assert executed.status_code == 200, executed.text
    toolsets = executed.json()["config"]["toolsets"]
    assert "configuration" not in toolsets and toolsets["web"]["enabled"] is False
    # The override replaced the whole selection, so the revision's other group is off for this run.
    assert [group for group in PLATFORM_GROUPS if toolsets[group]["enabled"]] == ["execution"]
    assert permissions(toolsets["execution"]) == {("read", "inherit"), ("write", "allow")}


@pytest.mark.parametrize("invalid_index", [False, True])
def test_run_search_migration_retries_after_concurrent_index_build(
    empty_database: Database, invalid_index: bool
) -> None:
    upgrade(empty_database, OSS)
    with migration_connection(empty_database, OSS) as config:
        # The additive DDL and concurrent build committed, but the process died before stamping its revision.
        command.downgrade(config, "4d51a94777d9")
        command.stamp(config, "2e9655a2d223")
        if invalid_index:
            connection = config.attributes["connection"]
            connection.execute(
                text("UPDATE pg_index SET indisvalid = false WHERE indexrelid = 'ix_runs_search_vector'::regclass")
            )
            connection.commit()
    upgrade(empty_database, OSS)
    check(empty_database, OSS)
    assert rows(
        empty_database, "SELECT indisvalid::text FROM pg_index WHERE indexrelid = 'ix_runs_search_vector'::regclass"
    ) == [("true",)]
