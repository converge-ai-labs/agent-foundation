"""Direct setup preserves identity isolation, CLI guards and resumable provisioning."""

from unittest.mock import Mock

import httpx2
import pytest
from a13n_service.configuration.sources import load_settings
from a13n_service.database.metadata import service_metadata
from a13n_service.iam.models import OrganizationRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from sqlalchemy import func, select

from ..infrastructure import bootstrap, core_resources
from ..infrastructure.client import LiveClient
from ..infrastructure.round_two_lab import identity


@pytest.mark.anyio
async def test_seed_two_workspaces_is_idempotent_and_keeps_second_user_scoped(
    monkeypatch, tmp_path, owned_postgres_url
):
    monkeypatch.setenv("A13N_SERVICE_DATABASE_URL", "must-not-use-ambient-database")
    settings = load_settings(environ={}, overrides={"database": {"url": owned_postgres_url}})
    engine = create_sql_engine(settings.database_config())
    try:
        async with engine.begin() as connection:
            await connection.run_sync(service_metadata().create_all)
        migrator = Mock()
        monkeypatch.setattr(bootstrap, "DatabaseMigrator", Mock(return_value=migrator))
        config = {**identity(), "workspace_root": str(tmp_path / "workspace")}
        config["other_identity"] = {
            **identity(),
            "organization_id": config["organization_id"],
            "workspace_only": True,
        }
        await bootstrap.bootstrap(settings, config)
        await bootstrap.bootstrap(settings, config)
        async with create_session_factory(engine)() as session:
            for model, count in (
                (OrganizationRecord, 1),
                (UserRecord, 2),
                (WorkspaceRecord, 2),
                (RoleBindingRecord, 4),
            ):
                assert await session.scalar(select(func.count()).select_from(model)) == count
            bindings = list(await session.scalars(select(RoleBindingRecord)))
            assert {(b.resource_type, b.role_key) for b in bindings if b.principal_id == config["user_id"]} == {
                ("organization", "admin"),
                ("workspace", "admin"),
            }
            other = config["other_identity"]
            assert {
                (b.resource_type, b.role_key, b.workspace_id) for b in bindings if b.principal_id == other["user_id"]
            } == {
                ("organization", "member", None),
                ("workspace", "admin", other["workspace_id"]),
            }
        assert migrator.upgrade.call_count == 2
        migrator.current.assert_not_called()
    finally:
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("migrate", [False, True])
async def test_migration_failure_prevents_identity_writes(monkeypatch, migrate):
    migrator = Mock()
    (migrator.upgrade if migrate else migrator.current).side_effect = RuntimeError("migration failed")
    monkeypatch.setattr(bootstrap, "DatabaseMigrator", Mock(return_value=migrator))
    engine = Mock()
    monkeypatch.setattr(bootstrap, "create_sql_engine", engine)
    with pytest.raises(RuntimeError, match="migration failed"):
        await bootstrap.initialize(load_settings(environ={}), [], migrate=migrate)
    engine.assert_not_called()
    if not migrate:
        migrator.current.assert_called_once_with(check_heads=True)


@pytest.mark.anyio
async def test_core_provisioning_resumes_saved_progress_with_stable_idempotency_keys():
    config = {**identity(), "control_url": "http://127.0.0.1", "workspace_root": "/tmp/live-workspace"}
    requests, saved = [], []

    def response(request):
        requests.append(request)
        if len(requests) == 3:
            return httpx2.Response(503, json={})
        result = {"id": f"resource-{len(requests)}"}
        return httpx2.Response(201, json={"agent": result} if request.url.path.endswith("/agents") else result)

    async with httpx2.AsyncClient(base_url=config["control_url"], transport=httpx2.MockTransport(response)) as http:
        client = LiveClient(config, http)
        with pytest.raises(AssertionError):
            await core_resources.provision(client, on_created=lambda value: saved.append(dict(value)))
        assert saved[-1]["model_id"] == "resource-2"
        assert "environment_provider_id" not in saved[-1]
        client.config = dict(saved[-1])
        await core_resources.provision(client, on_created=lambda value: saved.append(dict(value)))
        await core_resources.provision(client)
    assert len(requests) == 8
    assert len(saved) == 7
    keys = [request.headers["Idempotency-Key"] for request in requests]
    assert keys[2] == keys[3]
    assert len(set(keys)) == 7
    assert all(key.startswith("live-setup-" + config["workspace_id"]) for key in keys)
    assert all(request.url.path.startswith(f"/api/v1/workspaces/{config['workspace_id']}/") for request in requests)
