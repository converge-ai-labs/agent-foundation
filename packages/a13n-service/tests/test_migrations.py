"""The generated revisions build exactly the schema the tests use: tables, rules and all."""

import json
import time
from concurrent.futures import ThreadPoolExecutor

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


@pytest.mark.parametrize("invalid_index", [False, True])
def test_run_usage_index_retries_after_concurrent_build(empty_database: Database, invalid_index: bool) -> None:
    upgrade(empty_database, OSS)
    with migration_connection(empty_database, OSS) as config:
        command.downgrade(config, "48a66dd56558")
        # The build committed but the process stopped before stamping the revision.
        command.stamp(config, "a9a9c6399e27")
        if invalid_index:
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
    with migration_connection(empty_database, OSS) as config:
        command.downgrade(config, "a9a9c6399e27")
    assert rows(empty_database, "SELECT to_regclass('ix_runs_workspace_started')::text") == [(None,)]
    upgrade(empty_database, OSS)
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


def test_compaction_revision_roundtrip(empty_database: Database) -> None:
    with migration_connection(empty_database, OSS) as config:
        command.upgrade(config, "1c461368ce87")
    original_rules = rows(empty_database, RULES)
    original_objects = rows(empty_database, OBJECTS)
    with migration_connection(empty_database, OSS) as config:
        command.upgrade(config, "a9a9c6399e27")
    assert rows(empty_database, "SELECT version_num FROM alembic_version") == [("a9a9c6399e27",)]
    # This revision is no longer the head; metadata parity is checked after the full upgrade below.
    with migration_connection(empty_database, OSS) as config:
        command.downgrade(config, "1c461368ce87")
    assert rows(empty_database, RULES) == original_rules
    assert rows(empty_database, OBJECTS) == original_objects
    upgrade(empty_database, OSS)
    check(empty_database, OSS)


def test_monitoring_revision_upgrades_and_restores_main_schema(empty_database: Database) -> None:
    with migration_connection(empty_database, OSS) as config:
        command.upgrade(config, "9cc1d8d6a9c0")
    original_rules = rows(empty_database, RULES)
    original_objects = rows(empty_database, OBJECTS)
    columns = (
        "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = 'environments' ORDER BY ordinal_position"
    )
    original_columns = rows(empty_database, columns)
    with migration_connection(empty_database, OSS) as config:
        # Exercise monitoring independently of later additive revisions.
        command.upgrade(config, "36c442fa1a5e")
    assert rows(empty_database, "SELECT version_num FROM alembic_version") == [("36c442fa1a5e",)]
    with migration_connection(empty_database, OSS) as config:
        # Retire only the monitoring branch; keep main's occurrence completion schema.
        command.downgrade(config, "7d5bc53a5535@33e2cc388478")
    assert rows(empty_database, "SELECT version_num FROM alembic_version") == [("9cc1d8d6a9c0",)]
    assert rows(empty_database, RULES) == original_rules
    assert rows(empty_database, OBJECTS) == original_objects
    assert rows(empty_database, columns) == original_columns
    upgrade(empty_database, OSS)
    check(empty_database, OSS)


def test_schedule_revision_upgrades_and_restores_main_schema(empty_database: Database) -> None:
    with migration_connection(empty_database, OSS) as config:
        command.upgrade(config, "fed344f2d755")
    original_rules = rows(empty_database, RULES)
    original_objects = rows(empty_database, OBJECTS)
    # Exercise schedules independently of later monitoring revisions.
    with migration_connection(empty_database, OSS) as config:
        command.upgrade(config, "33e2cc388478")
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


@pytest.mark.anyio
async def test_once_migration_preserves_cron_rows_and_refuses_lossy_downgrade(database, runtime, tenant):  # type: ignore[no-untyped-def]
    from datetime import timedelta

    from a13n_service.infra.db import now
    from a13n_service.runs.schedules.tables import ScheduleRow

    async with transaction(runtime.storage) as session:
        owner = SessionRow(
            id="ses_schedule_migration",
            organization_id=tenant.organization_id,
            workspace_id=tenant.workspace_id,
            created_by_id=tenant.principal_id,
            labels={},
        )
        session.add(owner)
        await session.flush()
        session.add(
            ScheduleRow(
                id="sch_migration",
                organization_id=tenant.organization_id,
                workspace_id=tenant.workspace_id,
                session_id=owner.id,
                name="Retained cron",
                labels={},
                state="paused",
                pause_reason="manual",
                cron_expression="0 9 * * *",
                timezone="UTC",
                max_lateness_seconds=300,
                invocation={},
                principal_id=tenant.principal_id,
                authority={},
                created_by_id=tenant.principal_id,
                updated_by_id=tenant.principal_id,
                create_request_key="migration",
                create_request_digest="migration",
            )
        )
    query = "SELECT id, session_id, cron_expression, timezone, version::text FROM schedules"
    original = rows(database, query)
    with migration_connection(database, OSS) as config:
        command.downgrade(config, "33e2cc388478")
    assert rows(database, query) == original
    upgrade(database, OSS)
    assert rows(database, query) == original
    assert rows(database, "SELECT schedule_type, run_at FROM schedules") == [("cron", None)]
    check(database, OSS)

    async with transaction(runtime.storage) as session:
        schedule = await session.get_one(ScheduleRow, "sch_migration")
        schedule.schedule_type = "once"
        schedule.run_at = await now(session) + timedelta(hours=1)
        schedule.cron_expression = schedule.timezone = None
    with pytest.raises(DBAPIError, match="cannot downgrade while one-time schedules exist"):
        with migration_connection(database, OSS) as config:
            command.downgrade(config, "33e2cc388478")
    assert rows(database, "SELECT schedule_type FROM schedules") == [("once",)]
    check(database, OSS)


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
    # Synchronous migration DDL must not compete with background transactions on this database.
    await runs_kit.pause_sweeps(service)
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


@pytest.mark.anyio
async def test_completion_migration_preserves_submission_and_refuses_lossy_downgrade(
    database, service, scripted_model, runs_kit
):  # type: ignore[no-untyped-def]
    from a13n_service.runs.schedules import dispatch

    from .test_schedule_dispatch import schedule_for, trigger

    schedule, _ = await schedule_for(service, scripted_model, runs_kit)
    await trigger(service, schedule, runs_kit)
    await dispatch.process(service.runtime, schedule["id"])
    query = "SELECT id, status, thread_id, entry_id, finished_at::text, version::text FROM schedule_occurrences"
    original = rows(database, query)
    with migration_connection(database, OSS) as config:
        command.downgrade(config, "63a5d892fc55")
    assert rows(database, query) == original
    upgrade(database, OSS)
    assert rows(database, query) == original
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    assert rows(database, "SELECT status, execution_outcome FROM schedule_occurrences") == [("completed", "succeeded")]
    with pytest.raises(DBAPIError, match="cannot downgrade while completed occurrences exist"):
        with migration_connection(database, OSS) as config:
            command.downgrade(config, "63a5d892fc55")
    check(database, OSS)


@pytest.mark.anyio
async def test_retained_execution_revision_roundtrip_and_refuses_data_loss(database, runtime, tenant):
    from a13n_service.runs.tables import ThreadCodeActRow

    before_rules, before_objects = rows(database, RULES), rows(database, OBJECTS)
    with migration_connection(database, OSS) as config:
        command.downgrade(config, "82c26d512c94")
    assert rows(database, "SELECT to_regclass('public.tool_executions')::text") == [(None,)]
    upgrade(database, OSS)
    assert rows(database, RULES) == before_rules
    assert rows(database, OBJECTS) == before_objects
    async with transaction(runtime.storage) as session:
        parent = SessionRow(
            id="ses_retained_migration",
            organization_id=tenant.organization_id,
            workspace_id=tenant.workspace_id,
            created_by_id=tenant.principal_id,
            labels={},
        )
        session.add(parent)
        await session.flush()
        thread = new_thread(parent, mcp_headers={}, message_history=[])
        session.add(thread)
        await session.flush()
        session.add(ThreadCodeActRow(thread_id=thread.id, values={"saved": 42}))
    with pytest.raises(RuntimeError, match="Retained execution data prevents downgrade"):
        with migration_connection(database, OSS) as config:
            command.downgrade(config, "82c26d512c94")
    assert rows(database, "SELECT values::text FROM thread_codeact") == [('{"saved": 42}',)]
    check(database, OSS)


@pytest.mark.anyio
async def test_schedule_target_migration_restores_waiting_execution_without_changing_history(
    database, service, scripted_model, runs_kit
):  # type: ignore[no-untyped-def]
    from a13n_service.infra.db import short_session
    from a13n_service.runs.schedules import dispatch
    from a13n_service.runs.schedules.tables import OccurrenceRow
    from a13n_service.runs.tables import RunRow, ThreadRow

    from .test_schedule_dispatch import schedule_for, trigger

    schedule, _ = await schedule_for(service, scripted_model, runs_kit, user_questions=True)
    occurrence = await trigger(service, schedule, runs_kit)
    await dispatch.process(service.runtime, schedule["id"])
    scripted_model.call("ask_user_question", {"questions": [runs_kit.QUESTION]}, call_id="migration_wait")
    await (await runs_kit.attempt(service))
    async with short_session(service.runtime.storage) as session:
        item = await session.get_one(OccurrenceRow, occurrence["id"])
        thread = await session.get_one(ThreadRow, item.thread_id)
        waiting = await session.get_one(RunRow, thread.last_run_id)
        assert waiting.status == "waiting"
        entry_id = item.entry_id
    resumed_before = await service.client.post(
        f"{service.api}/runs/{waiting.id}/resume",
        json={"approvals": {}, "calls": {"migration_wait": {"status": "returned", "value": {"response": "blue"}}}},
        headers=runs_kit.fresh_key(),
    )
    assert resumed_before.status_code == 201, resumed_before.text
    scripted_model.call("ask_user_question", {"questions": [runs_kit.QUESTION]}, call_id="migration_again")
    await (await runs_kit.attempt(service))
    query = "SELECT id, status, version::text, updated_at::text FROM runs ORDER BY id"
    before = rows(database, query)
    with migration_connection(database, OSS) as config:
        command.downgrade(config, "707e22950c7f")
    upgrade(database, OSS)
    assert rows(database, query) == before
    assert rows(database, "SELECT occurrence_trigger_entry_id FROM runs") == [(entry_id,), (entry_id,)]
    assert rows(database, "SELECT schedule_occurrence_id FROM inbox_entries") == [(occurrence["id"],)]
    resumed = await service.client.post(
        f"{service.api}/runs/{resumed_before.json()['id']}/resume",
        json={"approvals": {}, "calls": {"migration_again": {"status": "returned", "value": {"response": "blue"}}}},
        headers=runs_kit.fresh_key(),
    )
    assert resumed.status_code == 201, resumed.text
    scripted_model.say("After migration")
    await (await runs_kit.attempt(service))
    assert rows(database, "SELECT status, completion_run_id FROM schedule_occurrences") == [
        ("completed", resumed.json()["id"])
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("rejected", "outcome"), [(False, "succeeded"), (True, "succeeded"), (True, "failed"), (True, "cancelled")]
)
async def test_schedule_target_migration_records_legacy_completion_without_reassigning_manual_runs(
    database, service, scripted_model, runs_kit, monkeypatch, rejected, outcome
):  # type: ignore[no-untyped-def]
    from dataclasses import replace
    from unittest.mock import AsyncMock

    from a13n_service.infra.db import now
    from a13n_service.infra.errors import ServiceError
    from a13n_service.runs import seal
    from a13n_service.runs.schedules import completion, dispatch
    from a13n_service.runs.schemas import Outcome
    from a13n_service.runs.tables import RunRow, ThreadRow

    from .test_schedule_dispatch import observed_execution, schedule_for, trigger
    from .test_schedule_webhooks import subscribe

    schedule, agent = await schedule_for(service, scripted_model, runs_kit)
    await subscribe(service, schedule_id=schedule["id"])
    occurrence = await trigger(service, schedule, runs_kit)
    runtime = service.runtime
    if rejected:
        admission = AsyncMock()
        admission.accept.side_effect = ServiceError("forbidden", "Admission refused")
        runtime = replace(runtime, admission=admission)
    await dispatch.process(runtime, schedule["id"])
    stored = (
        await service.client.get(f"{service.api}/schedules/{schedule['id']}/occurrences/{occurrence['id']}")
    ).json()
    initial = await observed_execution(service, schedule, occurrence["id"])
    if rejected:
        assert initial["initial_entry_status"] == "failed" and initial["initial_run"] is None
        manual = await runs_kit.submit(service, stored["thread_id"], runs_kit.message(agent, "Manual recovery"))
        assert manual.status_code == 201, manual.text
        final_id = manual.json()["run"]["id"]
    else:
        final_id = initial["initial_run"]["id"]
    # Simulate the old lifecycle: final Runs did not persist occurrence completion.
    with monkeypatch.context() as patch:
        patch.setattr(completion, "complete", AsyncMock())
        if outcome == "succeeded":
            scripted_model.say("Historical result")
            await (await runs_kit.attempt(service))
        else:
            async with transaction(service.runtime.storage) as session:
                thread = await session.get_one(ThreadRow, stored["thread_id"], with_for_update=True)
                run = await session.get_one(RunRow, final_id, with_for_update=True)
                result = Outcome.cancelled() if outcome == "cancelled" else Outcome.failed("test", "Historical failure")
                await seal.seal(session, service.runtime, thread, run, None, result, at=await now(session))
        later = await runs_kit.submit(service, stored["thread_id"], runs_kit.message(agent, "Later conversation"))
        assert later.status_code == 201, later.text
        scripted_model.say("Later result")
        await (await runs_kit.attempt(service))
    runs_query = "SELECT id, status, source_entry_id, version::text, updated_at::text FROM runs ORDER BY id"
    runs_before = rows(database, runs_query)
    notices_query = "SELECT id, payload::text FROM outbox WHERE kind = 'webhook' ORDER BY id"
    notices_before = rows(database, notices_query)
    with migration_connection(database, OSS) as config:
        command.downgrade(config, "707e22950c7f")
    upgrade(database, OSS)
    migrated = (
        await service.client.get(f"{service.api}/schedules/{schedule['id']}/occurrences/{occurrence['id']}")
    ).json()
    assert migrated["status"] == "completed" and migrated["execution_outcome"] == outcome
    assert migrated["completion_run_id"] == final_id
    final = (await service.client.get(f"{service.api}/runs/{final_id}")).json()
    assert migrated["completed_at"] == final["sealed_at"]
    for field in ("session_id", "thread_id", "entry_id", "finished_at", "snapshot", "schedule_version"):
        assert migrated[field] == stored[field]
    assert migrated["version"] == stored["version"] + 1
    assert migrated["updated_at"] > stored["updated_at"]
    assert rows(database, runs_query) == runs_before
    assert rows(database, notices_query) == notices_before
    assert not (await observed_execution(service, schedule, occurrence["id"]))["has_unfinished_work"]
    assert rows(database, "SELECT count(*)::text FROM runs WHERE occurrence_trigger_entry_id IS NOT NULL") == [
        ("0" if rejected else "1",)
    ]
    assert (
        final["schedule_origin"] is None if rejected else final["schedule_origin"]["occurrence_id"] == occurrence["id"]
    )
    # Reopening the same revision does not change already completed history or emit notifications.
    completion_query = "SELECT id, status, version::text, updated_at::text, completion_run_id FROM schedule_occurrences"
    completed_before = rows(database, completion_query)
    with migration_connection(database, OSS) as config:
        command.downgrade(config, "707e22950c7f")
    upgrade(database, OSS)
    assert rows(database, completion_query) == completed_before
    assert rows(database, notices_query) == notices_before
    next_item = await trigger(service, schedule, runs_kit, key="after-migration")
    assert next_item["status"] == "pending"
    changed = await service.client.patch(
        f"{service.api}/schedules/{schedule['id']}",
        json={"target": {"mode": "thread"}},
        headers=runs_kit.if_match(schedule),
    )
    assert changed.status_code == 200, changed.text
    with pytest.raises(DBAPIError, match="cannot downgrade while reusable or changed schedule targets exist"):
        with migration_connection(database, OSS) as config:
            command.downgrade(config, "707e22950c7f")
    check(database, OSS)


def test_schedule_target_migration_replaces_unique_session_index(database):  # type: ignore[no-untyped-def]
    def session_indexes():
        return rows(
            database,
            """
            SELECT c.relname, i.indisunique::text, pg_get_indexdef(i.indexrelid, 1, true)
            FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
            WHERE i.indrelid = 'schedules'::regclass
              AND pg_get_indexdef(i.indexrelid, 1, true) = 'session_id'
            ORDER BY c.relname
        """,
        )

    assert session_indexes() == [("ix_schedules_session_id", "false", "session_id")]
    with migration_connection(database, OSS) as config:
        command.downgrade(config, "707e22950c7f")
    assert session_indexes() == [("uq_schedules_session_id", "true", "session_id")]
    upgrade(database, OSS)
    assert session_indexes() == [("ix_schedules_session_id", "false", "session_id")]
    check(database, OSS)
