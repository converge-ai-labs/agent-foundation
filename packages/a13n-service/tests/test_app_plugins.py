"""Installed App registration, organization admission and accepted Run activation."""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace

import httpx2
import pytest
from a13n_service import app as assembly
from a13n_service.app_definitions import AppDefinition, InstalledApp, OrganizationSweep
from a13n_service.app_plugin_runtime import app_sweeps
from a13n_service.app_plugins import (
    AppActivation,
    AppCatalog,
    AppPolicy,
    Frontend,
)
from a13n_service.distribution import OSS, Distribution
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.agent import ResolvedSubagent, build
from a13n_service.runs.claim import claim
from a13n_service.runs.execute import _plan
from a13n_service.runs.tables import RunRow
from a13n_service.settings import Settings
from a13n_service.tenancy.app_plugins import app_router
from a13n_service.tenancy.tables import OrganizationRow
from fastapi import Depends
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from .conftest import EMAIL, PASSWORD, if_match
from .test_agents import HELPER, HELPER_REVISION, PLUGINS, config, resolved
from .test_tenancy import join

pytestmark = pytest.mark.anyio

APP_ID = "test.reports"


def installed_apps(*, version="1.0", routers=(), frontend=None, sweeps=(), startup=None):
    return (
        InstalledApp(
            AppDefinition(
                app_id=APP_ID,
                name="Reports",
                factory_keys=frozenset({"test.audit"}),
                routers=routers,
                frontend=frontend,
                sweeps=sweeps,
                startup=startup,
            ),
            "test-reports",
            version,
        ),
    )


def catalog(**kwargs):
    return AppCatalog(tuple(app.manifest for app in installed_apps(**kwargs)))


@pytest.fixture
async def apps_service(serve, monkeypatch, tmp_path):
    calls = []
    router = app_router(APP_ID)

    async def dependency():
        calls.append("dependency")

    @router.post("/report", name="create_test_report", dependencies=[Depends(dependency)])
    async def report():
        calls.append("handler")
        return {"created": True}

    resource_events = []

    async def startup(runtime, stack):
        calls.append("startup")
        stack.callback(calls.append, "closed")
        stack.callback(resource_events.append, "resource-close")
        started = asyncio.Event()

        async def background():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                resource_events.append("task-cleanup")

        runtime.tasks.start(background(), name="app-resource-user", service=True)
        await started.wait()

    assets = tmp_path / "reports_assets"
    assets.mkdir()
    (assets / "__init__.py").write_text("")
    (assets / "static").mkdir()
    (assets / "static" / "index.js").write_text('import "./chunk.js"; export const bridge = 1;')
    (assets / "static" / "chunk.js").write_text('export const name = "Reports";')
    (assets / "static" / "index.css").write_text(".reports {color: blue}")
    monkeypatch.syspath_prepend(str(tmp_path))
    installed = installed_apps(
        routers=(router,), frontend=Frontend("reports_assets", "static", styles=("index.css",)), startup=startup
    )
    monkeypatch.setattr(assembly, "discover_apps", lambda: installed)
    monkeypatch.setattr(assembly, "build_harness_plugin_factory_catalog", lambda **_: PLUGINS)
    async with serve(distribution=OSS.extend(Distribution(name="apps-test"))) as service:
        service.calls, service.apps = calls, installed
        yield service
    assert calls[-1] == "closed"
    assert resource_events == ["task-cleanup", "resource-close"]


async def toggle(service, enabled):
    path = f"{service.organization}/app-plugins"
    current = await service.client.get(path)
    result = await service.client.put(f"{path}/{APP_ID}", json={"enabled": enabled}, headers=if_match(current.json()))
    assert result.status_code == 200, result.text
    return result


async def test_http_bootstrap_seeds_app_policy_once(settings: Settings) -> None:
    app = InstalledApp(AppDefinition(app_id=APP_ID, name="Reports"), "test-reports", "1.0")
    other = InstalledApp(AppDefinition(app_id="test.other", name="Other"), "test-other", "1.0")
    config = settings.model_copy(
        update={"plugins": settings.plugins.model_copy(update={"bootstrap_enabled_apps": (APP_ID, APP_ID)})}
    )
    service = assembly.build_app(role="control", settings=config, apps=(app, other))
    async with (
        service.router.lifespan_context(service),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=service), base_url="https://service.test") as client,
    ):
        await service.state.runtime.redis.flushdb()
        credentials = {"email": EMAIL, "password": PASSWORD}
        created = await client.post("/api/v1/auth/bootstrap", json=credentials)
        assert created.status_code == 200, created.text
        [org] = (await client.get("/api/v1/organizations")).json()["items"]
        path = f"/api/v1/organizations/{org['id']}/app-plugins"
        current = await client.get(path)
        assert {item["app_id"]: item["enabled"] for item in current.json()["items"]} == {
            APP_ID: True,
            "test.other": False,
        }
        disabled = await client.put(
            f"{path}/{APP_ID}",
            json={"enabled": False},
            headers={"If-Match": current.headers["etag"], "X-CSRF-Token": created.json()["csrf_token"]},
        )
        assert disabled.status_code == 200, disabled.text
        repeat = await client.post("/api/v1/auth/bootstrap", json=credentials)
        assert repeat.status_code == 409
        assert (await client.get(path)).json() == disabled.json()


async def test_policy_preconditions_authority_and_atomic_audit(apps_service):
    service = apps_service
    path = f"{service.organization}/app-plugins"
    async with transaction(service.runtime.storage) as session:
        organization = await session.get_one(OrganizationRow, service.tenant.organization_id)
        organization.settings = {"other": {"value": 1}}
    current = await service.client.get(path)
    assert current.headers["cache-control"] == "no-store"
    assert current.headers["etag"] == if_match(current.json())["if-match"]
    assert current.json()["items"][0]["enabled"] is False
    assert current.json()["items"][0]["frontend"] is None
    target = f"{path}/{APP_ID}"
    assert (await service.client.put(target, json={"enabled": True})).status_code == 428
    enabled = await toggle(service, True)
    assert enabled.json()["version"] == current.json()["version"] + 1
    stale = await service.client.put(target, json={"enabled": False}, headers=if_match(current.json()))
    assert stale.status_code == 412
    assert (await toggle(service, True)).json()["version"] == enabled.json()["version"]
    async with AsyncExitStack() as stack:
        viewer = await join(service, stack, "viewer@example.com", "viewer")
        read = await viewer.get(path)
        assert read.status_code == 200 and "admin" not in read.json()["permissions"]
        denied = await viewer.put(target, json={"enabled": False}, headers=if_match(read.json()))
        assert denied.status_code == 403
    async with short_session(service.runtime.storage) as session:
        organization = await session.get_one(OrganizationRow, service.tenant.organization_id)
        assert organization.settings == {"other": {"value": 1}, "app_plugins": {"enabled": [APP_ID]}}
        events = list(await session.scalars(select(AuditEventRow).where(AuditEventRow.action == "app_plugin.update")))
    assert sorted(event.outcome for event in events) == ["denied", "ok"]
    [changed] = [event for event in events if event.outcome == "ok"]
    assert changed.details == {"enabled": True}


async def test_policy_can_disable_an_uninstalled_retained_app_without_changing_other_settings(apps_service):
    service = apps_service
    retained = "retired.reports"
    async with transaction(service.runtime.storage) as session:
        organization = await session.get_one(OrganizationRow, service.tenant.organization_id)
        organization.settings = {"other": {"value": 1}, "app_plugins": {"enabled": [retained]}}
    path = f"{service.organization}/app-plugins"
    current = await service.client.get(path)
    response = await service.client.put(f"{path}/{retained}", json={"enabled": False}, headers=if_match(current.json()))
    assert response.status_code == 200, response.text
    assert response.json()["version"] == current.json()["version"] + 1
    async with short_session(service.runtime.storage) as session:
        organization = await session.get_one(OrganizationRow, service.tenant.organization_id)
        assert organization.settings == {"other": {"value": 1}, "app_plugins": {"enabled": []}}
        events = list(await session.scalars(select(AuditEventRow).where(AuditEventRow.target_id == retained)))
        assert len(events) == 1 and events[0].details == {"enabled": False}
    denied = await service.client.put(f"{path}/{retained}", json={"enabled": True}, headers=if_match(response.json()))
    assert denied.status_code == 404


async def test_guard_runs_before_dependencies_and_preserves_csrf_and_workspace_authority(apps_service):
    service = apps_service
    path = f"/api/v1/apps/{APP_ID}/report"
    assert service.calls == ["startup"]
    disabled = await service.client.post(path)
    assert disabled.status_code == 422, disabled.text
    assert service.calls == ["startup"]
    await toggle(service, True)
    csrf = service.client.headers.pop("x-csrf-token")
    assert (await service.client.post(path)).status_code == 403
    service.client.headers["x-csrf-token"] = csrf
    async with AsyncExitStack() as stack:
        viewer = await join(service, stack, "viewer@example.com", "viewer")
        private = await service.client.post(f"{service.organization}/workspaces", json={"name": "Private"})
        refused = await viewer.post(path, headers={"x-workspace-id": private.json()["id"]})
        assert refused.status_code == 403
    assert service.calls == ["startup"]
    allowed = await service.client.post(path)
    assert allowed.status_code == 200, allowed.text
    assert service.calls == ["startup", "dependency", "handler"]
    await toggle(service, False)
    assert (await service.client.post(path)).status_code == 422
    assert service.calls == ["startup", "dependency", "handler"]


async def test_packaged_assets_keep_chunks_css_real_404_and_immutable_cache(apps_service):
    service = apps_service
    enabled = await toggle(service, True)
    frontend = enabled.json()["items"][0]["frontend"]
    entry = await service.client.get(frontend["entry"])
    assert entry.status_code == 200 and "./chunk.js" in entry.text
    assert "immutable" in entry.headers["cache-control"]
    assert entry.headers["x-content-type-options"] == "nosniff"
    chunk = await service.client.get(frontend["entry"].replace("index.js", "chunk.js"))
    css = await service.client.get(frontend["styles"][0])
    assert chunk.status_code == css.status_code == 200
    assert "javascript" in chunk.headers["content-type"] and "text/css" in css.headers["content-type"]
    missing = await service.client.get(frontend["entry"].replace("index.js", "missing.js"))
    assert missing.status_code == 404 and "text/html" not in missing.headers["content-type"]
    assert (await service.client.get(frontend["entry"].replace("/1.0/", "/2.0/"))).status_code == 404
    for path in ("%00.js", "..%2F..%2Foutside.js", "a" * 300):
        response = await service.client.get(frontend["entry"].replace("index.js", path))
        assert response.status_code == 404, response.text
        assert "text/html" not in response.headers["content-type"]
    await toggle(service, False)
    # Public code is cached independently of business authority, as with core Console assets.
    assert (await service.client.get(frontend["entry"])).status_code == 200


async def test_development_assets_override_only_the_selected_app(serve, monkeypatch, tmp_path):
    for name, content in (("dev", "updated source"), ("packaged", "packaged source")):
        package = tmp_path / name
        package.mkdir()
        (package / "__init__.py").touch()
        (package / "static").mkdir()
        (package / "static" / "index.js").write_text(content)
    monkeypatch.syspath_prepend(str(tmp_path))
    apps = (
        InstalledApp(AppDefinition(APP_ID, "Dev", frontend=Frontend("dev", "missing")), "dev", "1.0"),
        InstalledApp(AppDefinition("other.app", "Other", frontend=Frontend("packaged", "static")), "other", "1.0"),
    )
    monkeypatch.setattr(assembly, "discover_apps", lambda: apps)

    def build(distribution, **kwargs):
        return assembly.build_app(distribution, dev_app=APP_ID, dev_assets=tmp_path / "dev/static", **kwargs)

    async with serve(service_app=build) as service:
        for app, cache, content in (
            (apps[0], "no-store", "updated source"),
            (apps[1], "public, max-age=31536000, immutable", "packaged source"),
        ):
            response = await service.client.get(app.manifest.asset_url("index.js"))
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == cache
            assert response.text == content


async def test_acceptance_freezes_policy_new_runs_refresh_and_snapshot_is_immutable(
    apps_service, runs_kit, scripted_model
):
    service = apps_service
    enabled = await toggle(service, True)
    agent = await runs_kit.create_agent(service, scripted_model)
    first = await runs_kit.start_thread(service, agent, "first")
    await toggle(service, False)
    second = await runs_kit.start_thread(service, agent, "second")
    async with short_session(service.runtime.storage) as session:
        old = await session.get_one(RunRow, first["run"]["id"])
        new = await session.get_one(RunRow, second["run"]["id"])
        accepted = AppActivation.model_validate(old.app_activation)
        assert accepted.organization_version == enabled.json()["version"]
        assert accepted.apps[0].enabled and not AppActivation.model_validate(new.app_activation).apps[0].enabled
    [lease] = await claim(service.runtime, worker_id="worker-test", worker_build="test", limit=1)
    plan = await _plan(service.runtime, lease)
    assert plan.app_activation == accepted
    # Planning/recovery uses this Run's column, not the subsequently changed organization settings.
    assert (await _plan(service.runtime, lease)).app_activation == accepted
    with pytest.raises(DBAPIError, match="accepted run selection is immutable"):
        async with transaction(service.runtime.storage) as session:
            await session.execute(update(RunRow).where(RunRow.id == lease.run_id).values(app_activation={}))
    mismatched = replace(service.runtime, apps=catalog(version="2.0"))
    with pytest.raises(ServiceError) as error:
        await _plan(mismatched, lease)
    assert error.value.details == {"reason": "app_inventory_mismatch", "app_id": APP_ID}


@pytest.mark.parametrize("enabled", [False, True])
def test_root_and_inline_nodes_share_snapshot_but_keep_local_declarations(enabled):
    selected = config(
        plugins=[{"instance_name": "audit", "plugin_key": "test.audit"}],
        subagents={"mode": "inline", "named": {"helper": {"agent_id": HELPER, "revision_id": HELPER_REVISION}}},
    )
    child = resolved(
        HELPER, HELPER_REVISION, config(plugins=[{"instance_name": "child-audit", "plugin_key": "test.audit"}])
    )
    root = resolved(
        "agt_test",
        "apr_test",
        selected,
        subagents={"helper": ResolvedSubagent(selected.subagents.named["helper"], child.revision_id, "Helps", child)},
    )
    installed = catalog()
    activation = installed.capture(AppPolicy(enabled=(APP_ID,) if enabled else ()), 3)
    executable = build(
        root,
        capabilities=lambda _: [],
        plugins=PLUGINS,
        instrumentation=None,
        apps=installed,
        app_activation=activation,
    )
    assert executable.plugin_activation.is_enabled("audit") is enabled
    assert executable.definition.subagents[0].agent.plugins[0].enabled is enabled
    # A missing worker catalog cannot reinterpret frozen disabled ownership as a standalone factory.
    if not enabled:
        rebuilt = build(
            root, capabilities=lambda _: [], plugins=PLUGINS, instrumentation=None, app_activation=activation
        )
        assert rebuilt.plugin_activation.disabled_ids == frozenset({"audit"})
    assert not AppActivation().factory_enabled("test.audit", installed)
    assert AppActivation().factory_enabled("standalone", installed)


@pytest.fixture
def app_sessions(monkeypatch):
    # Background services share the pool; inspect only this adapter's SQL scopes.
    sessions = []

    @asynccontextmanager
    async def tracked(storage):
        async with short_session(storage) as session:
            sessions.append(session)
            yield session

    monkeypatch.setattr("a13n_service.app_plugin_runtime.short_session", tracked)
    return sessions


async def test_background_adapter_selects_only_enabled_tenants_outside_sql(apps_service, app_sessions):
    service = apps_service
    calls = []

    async def sweep(runtime, organization_id):
        assert runtime is scoped
        assert app_sessions and all(not session.in_transaction() for session in app_sessions)
        calls.append(organization_id)

    apps = installed_apps(sweeps=(OrganizationSweep("reports", 1, 10, sweep),))
    scoped = replace(service.runtime, apps=AppCatalog(tuple(app.manifest for app in apps)))
    [job] = app_sweeps(scoped, apps)
    await job.run()
    assert calls == []
    await toggle(service, True)
    await job.run()
    assert calls == [service.tenant.organization_id]
    await toggle(service, False)
    await job.run()
    assert calls == [service.tenant.organization_id]


@pytest.mark.parametrize("failure", ["error", "cancel"])
async def test_background_callbacks_isolate_tenant_failures_and_preserve_cancellation(service, app_sessions, failure):
    organizations = ["org_app_first", "org_app_second"]
    async with transaction(service.runtime.storage) as session:
        session.add_all(
            [
                OrganizationRow(
                    id=organization_id, name=organization_id, settings={"app_plugins": {"enabled": [APP_ID]}}
                )
                for organization_id in organizations
            ]
        )
    calls = []

    async def sweep(runtime, organization_id):
        assert app_sessions and all(not session.in_transaction() for session in app_sessions)
        calls.append(organization_id)
        if organization_id == organizations[0]:
            if failure == "error":
                raise RuntimeError("First organization failed")
            raise asyncio.CancelledError

    apps = installed_apps(sweeps=(OrganizationSweep("reports", 1, 0.01, sweep),))
    settings = service.runtime.settings.model_copy(
        update={"control": service.runtime.settings.control.model_copy(update={"sweep_batch": 2})}
    )
    scoped = replace(service.runtime, settings=settings, apps=AppCatalog(tuple(app.manifest for app in apps)))
    [job] = app_sweeps(scoped, apps)
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await job.run()
        assert calls == organizations[:1]
        return
    with pytest.raises(ExceptionGroup) as error:
        await job.run()
    assert len(error.value.exceptions) == 1 and isinstance(error.value.exceptions[0], RuntimeError)
    assert organizations[0] in error.value.exceptions[0].__notes__[0]
    assert calls == organizations


async def test_background_admission_failure_does_not_skip_later_healthy_tenants(service, app_sessions):
    organizations = ["org_app_first", "org_app_second", "org_app_third"]
    async with transaction(service.runtime.storage) as session:
        for organization_id in organizations:
            selection = {"enabled": [APP_ID]}
            if organization_id == organizations[1]:
                selection["unsupported"] = True
            session.add(OrganizationRow(id=organization_id, name=organization_id, settings={"app_plugins": selection}))
    calls = []

    async def sweep(runtime, organization_id):
        assert app_sessions and all(not session.in_transaction() for session in app_sessions)
        calls.append(organization_id)

    apps = installed_apps(sweeps=(OrganizationSweep("reports", 1, 10, sweep),))
    scoped = replace(service.runtime, apps=AppCatalog(tuple(app.manifest for app in apps)))
    [job] = app_sweeps(scoped, apps)
    with pytest.raises(ExceptionGroup) as error:
        await job.run()
    assert len(error.value.exceptions) == 1
    assert organizations[1] in error.value.exceptions[0].__notes__[0]
    assert calls == [organizations[0], organizations[2]]


def test_catalog_rejects_duplicate_identity_ownership_and_unguarded_routes():
    from a13n_service.app_plugin_runtime import app_routers
    from fastapi import APIRouter

    [app] = catalog().apps
    with pytest.raises(ValueError, match="Duplicate App ID"):
        AppCatalog((app, app))
    other = InstalledApp(AppDefinition("other", "Other", factory_keys=frozenset({"test.audit"})), "other", "1")
    with pytest.raises(ValueError, match="factory ownership"):
        AppCatalog((app, other.manifest))
    router = APIRouter()
    router.get("/api/v1/apps/test.reports/unsafe")(lambda: {})
    with pytest.raises(ValueError, match="app_router"):
        app_routers(installed_apps(routers=(router,)))


async def test_disabled_pending_target_is_not_reenabled_or_dispatched(
    apps_service, runs_kit, scripted_model, monkeypatch
):
    from a13n_harness import AbstractHarnessPlugin
    from pydantic_ai import Tool
    from pydantic_ai.capabilities import Capability

    from .test_agents import _Factory

    service = apps_service
    effects = []

    def approve_report():
        effects.append("approved")
        return "done"

    class Reports(AbstractHarnessPlugin):
        @property
        def plugin_id(self):
            return "reports"

        def get_capabilities(self):
            return [Capability(id="reports-tools", tools=[Tool(approve_report, requires_approval=True)])]

    monkeypatch.setattr(_Factory, "create_plugin", lambda *_: Reports())
    await toggle(service, True)
    agent = await runs_kit.create_agent(
        service, scripted_model, plugins=[{"plugin_key": "test.audit", "instance_name": "reports"}]
    )
    scripted_model.call("approve_report", {}, call_id="report-call")
    initial = await runs_kit.start_thread(service, agent, "Create report")
    await (await runs_kit.attempt(service))
    waiting = await runs_kit.get_run(service, initial["run"]["id"])
    assert waiting["status"] == "waiting", waiting
    assert effects == []
    await toggle(service, False)
    response = await service.client.post(
        f"{service.api}/runs/{waiting['id']}/resume",
        json={"approvals": {"report-call": {"action": "approve"}}, "calls": {}},
        headers=runs_kit.fresh_key(),
    )
    assert response.status_code == 201, response.text

    # Disabled construction and execution must both remain absent on the successor.
    def forbidden(*_):
        raise AssertionError("Disabled plugin constructed")

    monkeypatch.setattr(_Factory, "create_plugin", forbidden)
    await (await runs_kit.attempt(service))
    failed = await runs_kit.get_run(service, response.json()["id"])
    assert failed["status"] == "failed", failed
    assert failed["failure"]["code"] == "deferred_surface_mismatch"
    assert effects == []
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(RunRow, failed["id"])
        assert not AppActivation.model_validate(row.app_activation).apps[0].enabled


def test_cli_discovers_once_before_app_settings_validation(tmp_path, monkeypatch, clean_environment):
    from a13n_service import cli
    from a13n_service.settings import Section
    from click.testing import CliRunner

    class ReportsSettings(Section):
        label: str

    app = InstalledApp(AppDefinition(APP_ID, "Reports", settings={"reports": ReportsSettings}), "test-reports", "1")
    discoveries, served = [], []

    def discover():
        discoveries.append(True)
        return (app,)

    monkeypatch.setattr(cli, "discover_apps", discover)
    monkeypatch.setattr(assembly, "discover_apps", lambda: pytest.fail("App definitions rediscovered"))
    monkeypatch.setattr(cli.uvicorn, "run", lambda application, **_: served.append(application))
    config = tmp_path / "service.toml"
    config.write_text('[reports]\nlabel = "Team reports"\n')
    result = CliRunner().invoke(cli.main, ["--config", str(config), "run", "--role", "control"])
    assert result.exit_code == 0, result.output
    assert discoveries == [True] and len(served) == 1
