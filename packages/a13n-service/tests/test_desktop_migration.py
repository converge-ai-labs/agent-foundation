"""Desktop cutover rejects live legacy state and preserves immutable history."""

import json

import pytest
from a13n_service.distribution import OSS
from a13n_service.migrations.runner import check, migration_connection, upgrade
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from .test_desktop_control import begin
from .test_desktop_control import desktop as desktop
from .test_migrations import rows

pytestmark = pytest.mark.anyio
OLD_HEAD = "29270d0290da"
NEW_HEAD = "b805ded3d96d"


@pytest.fixture
async def legacy(database, service, desktop):
    run = desktop[0]["run"]
    with migration_connection(database, OSS) as config:
        command.downgrade(config, OLD_HEAD)
    engine = create_engine(database.url.get_secret_value())
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO desktop_targets (key, holder_id, unmanaged_input) VALUES ('target', 'legacy', true)")
            )
            connection.execute(
                text(
                    "INSERT INTO desktop_assistance_requests "
                    "(id, workspace_id, run_id, environment_id, target_key, mount, reason, state, deadline) "
                    "VALUES ('legacy', :workspace, :run, :environment, 'target', 'workspace', 'Help', 'consumed', now())"
                ),
                {"workspace": service.tenant.workspace_id, "run": run["id"], "environment": desktop[1]},
            )
            connection.execute(
                text(
                    "INSERT INTO desktop_viewers (id, assistance_id, principal_id, session_id, generation, epoch, expires_at, revoked) "
                    "VALUES ('viewer', 'legacy', :principal, 'old-session', 'old-generation', 1, now(), true)"
                ),
                {"principal": service.tenant.principal_id},
            )
        yield engine, run
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE desktop_assistance_requests SET grant_id = 'grant'",
        "UPDATE desktop_assistance_requests SET controller_id = :principal",
        "UPDATE desktop_assistance_requests SET operation_id = 'operation'",
        "UPDATE desktop_assistance_requests SET lease_expires_at = now() - interval '1 day'",
        *[
            f"UPDATE desktop_assistance_requests SET state = '{state}'"
            for state in ("preparing", "claiming", "human_control", "revoking", "blocked")
        ],
        "UPDATE desktop_viewers SET revoked = false",
        "UPDATE desktop_targets SET inflight = 1",
        "INSERT INTO desktop_executions (run_id, attempt_id, supported, inflight) VALUES (:run, 'old-attempt', true, 1)",
        "INSERT INTO desktop_executions (run_id, attempt_id, supported, uncertain) VALUES (:run, 'old-attempt', true, true)",
    ],
)
async def test_cutover_refuses_unsettled_legacy_state(database, service, legacy, mutation):
    engine, run = legacy
    with engine.begin() as connection:
        # Historical fixtures can reference old attempts without invoking the new worker.
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(text(mutation), {"principal": service.tenant.principal_id, "run": run["id"]})
    before = rows(database, "SELECT to_jsonb(r)::text FROM desktop_assistance_requests r")
    with pytest.raises(DBAPIError, match="Desktop cutover requires verified legacy"):
        upgrade(database, OSS)
    assert rows(database, "SELECT version_num FROM alembic_version") == [(OLD_HEAD,)]
    assert rows(database, "SELECT to_regclass('desktop_controls')::text") == [(None,)]
    assert rows(database, "SELECT to_jsonb(r)::text FROM desktop_assistance_requests r") == before


@pytest.mark.parametrize(
    "pending",
    [
        {"desktop_control_id": "legacy", "calls": [], "approvals": []},
        {"calls": [{"tool_name": "request_desktop_assistance"}], "approvals": []},
        {"calls": [], "approvals": [{"tool_name": "request_desktop_assistance"}]},
    ],
)
async def test_cutover_refuses_latest_sealed_desktop_wait(database, legacy, pending):
    engine, run = legacy
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(
            text(
                "UPDATE runs SET status = 'waiting', sealed_at = now(), wait_reason = 'desktop_control', "
                "pending = CAST(:pending AS jsonb), checkpoint = '{}'::jsonb, tail = '{}'::jsonb WHERE id = :run"
            ),
            {"pending": json.dumps(pending), "run": run["id"]},
        )
        connection.execute(
            text("UPDATE threads SET current_run_id = NULL, last_run_id = :run WHERE id = :thread"),
            {"run": run["id"], "thread": run["thread_id"]},
        )
    with pytest.raises(DBAPIError, match="Desktop cutover requires verified legacy"):
        upgrade(database, OSS)
    assert rows(database, "SELECT version_num FROM alembic_version") == [(OLD_HEAD,)]


async def test_cutover_preserves_historical_wait_after_a_new_run(database, service, desktop, runs_kit):
    run = desktop[0]["run"]
    response = await service.client.post(f"{service.api}/runs/{run['id']}/interrupt")
    assert response.status_code == 200, response.text
    response = await runs_kit.submit(service, run["thread_id"], runs_kit.message({"id": run["agent_id"]}, "New work"))
    assert response.status_code == 201, response.text
    with migration_connection(database, OSS) as config:
        command.downgrade(config, OLD_HEAD)
    engine = create_engine(database.url.get_secret_value())
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(
            text(
                "UPDATE runs SET status = 'waiting', failure = NULL, wait_reason = 'desktop_control', "
                "pending = '{\"desktop_control_id\":\"legacy\"}'::jsonb, checkpoint = '{}'::jsonb, tail = '{}'::jsonb "
                "WHERE id = :run"
            ),
            {"run": run["id"]},
        )
    queries = [
        f"SELECT to_jsonb(t)::text FROM {table} t ORDER BY id"
        for table in ("runs", "threads", "agent_revisions", "inbox_entries")
    ]
    before = [rows(database, query) for query in queries]
    upgrade(database, OSS)
    assert [rows(database, query) for query in queries] == before
    assert rows(database, "SELECT to_regclass('desktop_assistance_requests')::text") == [(None,)]
    check(database, OSS)
    try:
        with pytest.raises(DBAPIError, match="sealed run facts are immutable"):
            with engine.begin() as connection:
                connection.execute(text("UPDATE runs SET pending = '{}'::jsonb WHERE id = :run"), {"run": run["id"]})
    finally:
        engine.dispose()


async def test_cutover_accepts_revoked_records_without_rewriting_run_selection(database, legacy):
    before = rows(database, "SELECT to_jsonb(r)::text FROM runs r")
    upgrade(database, OSS)
    assert rows(database, "SELECT to_jsonb(r)::text FROM runs r") == before
    assert rows(database, "SELECT version_num FROM alembic_version") == [(NEW_HEAD,)]
    check(database, OSS)


async def test_downgrade_refuses_live_computer_control_without_changing_data(database, service, desktop):
    await begin(service, desktop)
    before = rows(database, "SELECT to_jsonb(c)::text FROM desktop_controls c")
    with pytest.raises(DBAPIError, match="Cannot downgrade Computer-scoped desktop data"):
        with migration_connection(database, OSS) as config:
            command.downgrade(config, OLD_HEAD)
    assert rows(database, "SELECT version_num FROM alembic_version") == [(NEW_HEAD,)]
    assert rows(database, "SELECT to_jsonb(c)::text FROM desktop_controls c") == before
    assert rows(database, "SELECT to_regclass('desktop_assistance_requests')::text") == [(None,)]


@pytest.mark.parametrize("table", ["runs", "inbox_entries", "outbox"])
async def test_downgrade_preserves_handback_history_after_control_pruning(database, service, desktop, table):
    engine = create_engine(database.url.get_secret_value())
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL session_replication_role = replica"))
            if table == "outbox":
                connection.execute(
                    text(
                        "INSERT INTO outbox (id, organization_id, workspace_id, kind, dedupe_key, target, payload) "
                        "VALUES ('notice', :organization, :workspace, 'desktop_handback', 'notice', '{}'::jsonb, '{}'::jsonb)"
                    ),
                    {"organization": service.tenant.organization_id, "workspace": service.tenant.workspace_id},
                )
            elif table == "inbox_entries":
                connection.execute(
                    text(
                        "UPDATE inbox_entries SET kind = 'desktop_handback', agent_id = NULL, origin_run_id = assigned_run_id"
                    )
                )
            else:
                connection.execute(text("UPDATE runs SET trigger = 'desktop_handback'"))
        assert rows(database, "SELECT count(*) FROM desktop_controls") == [(0,)]
        before = rows(database, f"SELECT to_jsonb(t)::text FROM {table} t ORDER BY id")
        with pytest.raises(DBAPIError, match="Cannot downgrade Computer-scoped desktop data"):
            with migration_connection(database, OSS) as config:
                command.downgrade(config, OLD_HEAD)
        assert rows(database, "SELECT version_num FROM alembic_version") == [(NEW_HEAD,)]
        assert rows(database, f"SELECT to_jsonb(t)::text FROM {table} t ORDER BY id") == before
    finally:
        engine.dispose()
