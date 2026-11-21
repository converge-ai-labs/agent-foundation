"""Desktop cutover rejects live legacy state and preserves immutable history."""

import pytest
from a13n_service.distribution import OSS
from a13n_service.migrations.runner import check, migration_connection, upgrade
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from .test_desktop_control import begin
from .test_desktop_control import desktop as desktop
from .test_migrations import OBJECTS, RULES, rows

pytestmark = pytest.mark.anyio
OLD_HEAD = "ed5de523fc26"
NEW_HEAD = "91abbf7b03c8"


def upgrade_desktop(database):
    """Exercise the desktop revision independently of later data cutovers."""
    with migration_connection(database, OSS) as config:
        command.upgrade(config, NEW_HEAD)


async def cancel_fixture_run(service, run_id):
    """Settle current-version work before crossing the Session binding migration."""
    response = await service.client.post(f"{service.api}/runs/{run_id}/interrupt")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"


@pytest.fixture
async def legacy(database, service, desktop):
    run = desktop[0]["run"]
    await cancel_fixture_run(service, run["id"])
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
        "UPDATE desktop_assistance_requests SET state = 'human_control'",
        "UPDATE desktop_viewers SET revoked = false",
    ],
)
async def test_cutover_refuses_unsettled_legacy_state(database, service, legacy, mutation):
    engine, run = legacy
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(text(mutation), {"principal": service.tenant.principal_id, "run": run["id"]})
    before = rows(database, "SELECT to_jsonb(r)::text FROM desktop_assistance_requests r")
    with pytest.raises(DBAPIError, match="Desktop cutover requires verified legacy"):
        upgrade_desktop(database)
    assert rows(database, "SELECT version_num FROM alembic_version") == [(OLD_HEAD,)]
    assert rows(database, "SELECT to_regclass('desktop_controls')::text") == [(None,)]
    assert rows(database, "SELECT to_jsonb(r)::text FROM desktop_assistance_requests r") == before


async def test_cutover_accepts_revoked_records_without_rewriting_run_selection(database, legacy):
    before = rows(database, "SELECT to_jsonb(r)::text FROM runs r")
    upgrade_desktop(database)
    assert rows(database, "SELECT to_jsonb(r)::text FROM runs r") == before
    assert rows(database, "SELECT version_num FROM alembic_version") == [(NEW_HEAD,)]


async def test_downgrade_refuses_live_computer_control_without_changing_data(database, service, desktop):
    await cancel_fixture_run(service, desktop[0]["run"]["id"])
    await begin(service, desktop)
    with migration_connection(database, OSS) as config:
        command.downgrade(config, NEW_HEAD)
    before = rows(database, "SELECT to_jsonb(c)::text FROM desktop_controls c")
    with pytest.raises(DBAPIError, match="Cannot downgrade Computer-scoped desktop data"):
        with migration_connection(database, OSS) as config:
            command.downgrade(config, OLD_HEAD)
    assert rows(database, "SELECT version_num FROM alembic_version") == [(NEW_HEAD,)]
    assert rows(database, "SELECT to_jsonb(c)::text FROM desktop_controls c") == before
    assert rows(database, "SELECT to_regclass('desktop_assistance_requests')::text") == [(None,)]


async def test_desktop_cutover_is_one_revision_from_legacy_and_roundtrips(empty_database):
    with migration_connection(empty_database, OSS) as config:
        scripts = ScriptDirectory.from_config(config)
        assert len(scripts.get_heads()) == 1
        revision = scripts.get_revision(NEW_HEAD)
        assert revision is not None and revision.down_revision == OLD_HEAD
        command.upgrade(config, OLD_HEAD)
    original_rules = rows(empty_database, RULES)
    original_objects = rows(empty_database, OBJECTS)

    upgrade_desktop(empty_database)
    assert rows(empty_database, "SELECT version_num FROM alembic_version") == [(NEW_HEAD,)]
    assert rows(
        empty_database,
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'desktop_%' ORDER BY tablename",
    ) == [("desktop_blocked_threads",), ("desktop_controls",), ("desktop_readers",)]

    with migration_connection(empty_database, OSS) as config:
        command.downgrade(config, OLD_HEAD)
    assert rows(empty_database, RULES) == original_rules
    assert rows(empty_database, OBJECTS) == original_objects
    upgrade(empty_database, OSS)
    check(empty_database, OSS)
