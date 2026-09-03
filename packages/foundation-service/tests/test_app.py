import asyncio
import hashlib
from base64 import b64encode
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from a13n_service.app import ServiceComponents, create_app
from a13n_service.connectivity.adapters import ConnectorAdapter, IngressAdapter
from a13n_service.connectivity.composition import AdapterDefinition, AdapterRegistry
from a13n_service.database import DatabaseMigrator
from a13n_service.plugins import BuiltinPluginArtifact, BuiltinPluginRegistration
from a13n_service.plugins.commands import (
    PluginRuntimeCatalogSnapshot,
    PluginRuntimeCommand,
)
from a13n_service.plugins.materialization import PluginRuntimeMaterializer
from a13n_service.plugins.models import PluginRecord, PluginVersionRecord
from a13n_service.plugins.on_demand import OnDemandPluginRuntime
from a13n_service.plugins.runner_supervisor import PluginRunnerSupervisor
from a13n_service.plugins.runtime import PluginRuntimeLock
from a13n_service.plugins.runtime_commands import PluginRuntimeCommandCoordinator
from a13n_service.plugins.runtime_resolver import FoundationPluginRuntimeCandidateResolver
from a13n_service.secrets import SecretProtectionError
from a13n_service.settings import ServiceRole, ServiceSettings
from a13n_service.skills import SkillRuntimePreparer
from a13n_service.storage import short_session
from a13n_service.trace_query import TraceQueryCapabilities, TraceQueryProviderRegistry
from fastapi import FastAPI

from .plugins.conftest import build_wheel, wheel_body


class _UnusedPluginRuntimeCandidateResolver:
    async def resolve_candidate(
        self,
        *,
        operation_id: str,
        command: PluginRuntimeCommand,
        catalog: PluginRuntimeCatalogSnapshot,
    ) -> PluginRuntimeLock:
        del operation_id, command, catalog
        raise AssertionError("no Plugin Runtime command was expected")

    async def require_candidate(self, *, runtime_lock_digest: str) -> PluginRuntimeLock:
        del runtime_lock_digest
        raise AssertionError("no Plugin Runtime command was expected")


class _UnusedPluginRuntimeStagingAuthority:
    async def stage_candidate(self, *, operation_id: str, runtime_lock: PluginRuntimeLock) -> str:
        del operation_id, runtime_lock
        raise AssertionError("no Plugin Runtime command was expected")

    async def activate_candidate(
        self,
        *,
        operation_id: str,
        runtime_lock: PluginRuntimeLock,
        staging_token: str,
        runtime_generation: int,
    ) -> None:
        del operation_id, runtime_lock, staging_token, runtime_generation
        raise AssertionError("no Plugin Runtime command was expected")

    async def abort_candidate(
        self,
        *,
        operation_id: str,
        runtime_lock: PluginRuntimeLock,
        staging_token: str | None,
    ) -> None:
        del operation_id, runtime_lock, staging_token
        raise AssertionError("no Plugin Runtime command was expected")


def request(app: FastAPI, path: str, *, method: str = "GET") -> httpx2.Response:
    async def send_request() -> httpx2.Response:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path)

    return asyncio.run(send_request())


def create_web_dist(directory: Path) -> Path:
    assets = directory / "assets"
    assets.mkdir(parents=True)
    (directory / "index.html").write_text("<!doctype html><title>Foundation Web</title>", encoding="utf-8")
    (assets / "app.js").write_text('document.title = "Foundation Web";', encoding="utf-8")
    return directory


def local_settings(tmp_path: Path, **updates: object) -> ServiceSettings:
    values: dict[str, object] = {
        "_env_file": None,
        "database_backend": "sqlite",
        "database_sqlite_path": tmp_path / "database.sqlite3",
        "redis_backend": "memory",
        "object_backend": "local",
        "object_local_root": tmp_path / "objects",
        "filesystem_root": tmp_path / "files",
        "secret_master_key_base64": b64encode(b"0123456789abcdef0123456789abcdef").decode(),
        "secret_encryption_key_id": "foundation-service-test-key",
        "connectivity_public_origin": "http://testserver",
        "connectivity_http_origins": ("http://testserver",),
    }
    values.update(updates)
    settings = ServiceSettings(**values)
    DatabaseMigrator(settings.database_config()).upgrade()
    return settings


def test_health_reports_process_role() -> None:
    response = request(create_app(ServiceSettings(_env_file=None, role=ServiceRole.worker)), "/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "role": "worker"}


def test_control_plane_openapi_uses_api_namespace() -> None:
    app = create_app(ServiceSettings(_env_file=None, role=ServiceRole.control, build_version="1.2.3"))

    response = request(app, "/api/openapi.json")

    assert response.status_code == 200
    document = response.json()
    assert document["info"] == {"title": "Agent Foundation Service", "version": "1.2.3"}
    assert "/healthz" not in document["paths"]
    assert "/readyz" not in document["paths"]
    schemas = document["components"]["schemas"]
    assert {
        "Asset",
        "Model",
        "Plugin",
        "PluginVersion",
        "Skill",
        "SkillPackageManifest",
        "SkillRevision",
    } <= schemas.keys()
    assert {"Observation", "TraceCollection", "TraceDetail", "TraceSummary"} <= schemas.keys()
    assert {
        "FoundationAgentSkillSelection",
        "ManagedSkillPackageManifest",
        "ModelResource",
        "WorkspaceSkill",
    }.isdisjoint(schemas)
    assert request(app, "/api/docs").status_code == 200
    assert request(app, "/api/docs/oauth2-redirect").status_code == 200
    assert request(app, "/openapi.json").status_code == 404
    assert request(app, "/docs/oauth2-redirect").status_code == 404
    assert "/api/v1/workspaces/{workspace_id}/traces" in document["paths"]
    assert "/api/v1/workspaces/{workspace_id}/traces/{trace_id}" in document["paths"]
    assert "/api/v1/plugins" in document["paths"]
    assert "/api/v1/plugins/{plugin_id}/versions" in document["paths"]
    assert "/api/v1/plugin-versions/{plugin_version_id}" in document["paths"]
    assert "/api/v1/plugin-versions/{plugin_version_id}/activate" in document["paths"]
    assert "/api/v1/plugins/{plugin_id}/deactivate" in document["paths"]
    assert "/api/v1/operations/{operation_id}" in document["paths"]


def test_web_application_serves_assets_and_browser_history(tmp_path: Path) -> None:
    web_dist = create_web_dist(tmp_path / "web")
    app = create_app(ServiceSettings(_env_file=None, role=ServiceRole.all, web_dist_dir=web_dist))

    assert "Foundation Web" in request(app, "/").text
    assert "Foundation Web" in request(app, "/executions/example").text
    assert request(app, "/assets/app.js").text == 'document.title = "Foundation Web";'
    assert request(app, "/assets/missing.js").status_code == 404
    assert request(app, "/assets/missing").status_code == 404
    assert request(app, "/healthz/").status_code == 404
    assert request(app, "/readyz/").status_code == 404
    assert request(app, "/executions/example", method="POST").status_code == 405


def test_web_fallback_never_handles_api_paths(tmp_path: Path) -> None:
    web_dist = create_web_dist(tmp_path / "web")
    app = create_app(ServiceSettings(_env_file=None, role=ServiceRole.all, web_dist_dir=web_dist))

    for path in ("/api", "/api/unknown"):
        response = request(app, path)
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == {"detail": "API route not found"}


def test_worker_role_serves_only_operational_endpoints(tmp_path: Path) -> None:
    web_dist = create_web_dist(tmp_path / "web")
    app = create_app(ServiceSettings(_env_file=None, role=ServiceRole.worker, web_dist_dir=web_dist))

    assert request(app, "/healthz").status_code == 200
    assert request(app, "/api/openapi.json").status_code == 404
    assert request(app, "/").status_code == 404


def test_connectivity_role_exposes_no_control_plane_routes() -> None:
    app = create_app(ServiceSettings(_env_file=None, role=ServiceRole.connectivity))

    assert request(app, "/healthz").json() == {"status": "ok", "role": "connectivity"}
    assert request(app, "/api/openapi.json").status_code == 404
    assert request(app, "/").status_code == 404


def test_connectivity_registries_are_copied_only_for_owning_roles() -> None:
    class Adapter:
        provider_key = "fake"
        driver_key = "fake"
        config_versions = frozenset({1})

    ingress_registry = AdapterRegistry[IngressAdapter]()
    connector_registry = AdapterRegistry[ConnectorAdapter]()
    ingress_registry.register(
        AdapterDefinition(
            key="fake",
            config_versions=frozenset({1}),
            factory=Adapter,
        )
    )
    connector_registry.register(
        AdapterDefinition(
            key="fake",
            config_versions=frozenset({1}),
            factory=Adapter,
        )
    )
    components = ServiceComponents(
        ingress_adapter_registry=ingress_registry,
        connector_adapter_registry=connector_registry,
    )

    control = create_app(ServiceSettings(_env_file=None, role=ServiceRole.control), components=components)
    connectivity = create_app(ServiceSettings(_env_file=None, role=ServiceRole.connectivity), components=components)
    worker = create_app(ServiceSettings(_env_file=None, role=ServiceRole.worker), components=components)
    ingress_registry.register(
        AdapterDefinition(
            key="later",
            config_versions=frozenset({1}),
            factory=Adapter,
        )
    )

    assert control.state.ingress_adapter_registry.keys() == ("fake",)
    assert connectivity.state.connector_adapter_registry.keys() == ("fake",)
    assert not hasattr(worker.state, "ingress_adapter_registry")
    assert not hasattr(worker.state, "connectivity_endpoint_policy")


def test_configured_web_build_requires_an_index(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Foundation Web index is missing"):
        create_app(ServiceSettings(_env_file=None, role=ServiceRole.control, web_dist_dir=tmp_path))


@pytest.mark.anyio
async def test_lifespan_constructs_storage_once_and_readiness_uses_it(tmp_path: Path) -> None:
    app = create_app(local_settings(tmp_path))

    async with app.router.lifespan_context(app):
        storage = app.state.storage
        assert app.state.db_engine is storage.engine
        assert app.state.db_session_factory is storage.sessions
        assert isinstance(app.state.skill_runtime_preparer, SkillRuntimePreparer)
        assert app.state.agent_plugin_selection_resolver is not None
        assert app.state.plugin_service is not None
        assert isinstance(app.state.plugin_runtime_materializer, PluginRuntimeMaterializer)
        assert isinstance(app.state.plugin_on_demand_runtime, OnDemandPluginRuntime)
        assert not hasattr(app.state, "plugin_runner_supervisor")
        assert app.state.trace_query_service is not None

        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/readyz")

        assert response.status_code == 200
        assert response.json() == {"status": "ready", "role": "all"}


@pytest.mark.anyio
async def test_control_lifespan_registers_distribution_builtin_plugins(tmp_path: Path) -> None:
    wheel = build_wheel()
    registration = BuiltinPluginRegistration(
        plugin_id="plg_builtinaudit0001",
        plugin_version_id="plgv_builtinauditv100",
        system_actor_id="sa_pluginrelease0001",
        plugin_key="acme.audit",
        distribution_name="acme-audit",
        top_level_package="acme_audit",
        version="1.0.0",
        content_digest=hashlib.sha256(wheel).hexdigest(),
        required=True,
    )
    app = create_app(
        local_settings(tmp_path, role=ServiceRole.control),
        components=ServiceComponents(
            builtin_plugin_artifacts=(
                BuiltinPluginArtifact(
                    registration=registration,
                    body_factory=lambda: wheel_body(wheel),
                    content_length=len(wheel),
                ),
            ),
        ),
    )

    async with app.router.lifespan_context(app):
        async with short_session(app.state.storage.sessions) as session:
            plugin = await session.get(PluginRecord, registration.plugin_id)
            version = await session.get(PluginVersionRecord, registration.plugin_version_id)

        assert plugin is not None and plugin.required is True
        assert plugin.source == "builtin"
        assert version is not None and version.content_digest == registration.content_digest


@pytest.mark.anyio
async def test_on_demand_import_failure_removes_worker_readiness(tmp_path: Path) -> None:
    app = create_app(local_settings(tmp_path, role=ServiceRole.worker))

    async with app.router.lifespan_context(app):
        app.state.plugin_on_demand_runtime = SimpleNamespace(ready=False)
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"detail": "plugin runtime unavailable"}


@pytest.mark.anyio
async def test_lifespan_rejects_partial_plugin_runtime_coordination(tmp_path: Path) -> None:
    app = create_app(
        local_settings(
            tmp_path,
            role=ServiceRole.control,
            plugin_runtime_mode="runner",
        ),
        components=ServiceComponents(
            plugin_runtime_candidate_resolver=_UnusedPluginRuntimeCandidateResolver(),
        ),
    )

    with pytest.raises(RuntimeError, match="requires both a candidate resolver and staging authority"):
        async with app.router.lifespan_context(app):
            pytest.fail("lifespan unexpectedly started")


@pytest.mark.anyio
async def test_lifespan_rejects_runner_coordination_in_on_demand_mode(tmp_path: Path) -> None:
    app = create_app(
        local_settings(tmp_path, role=ServiceRole.control),
        components=ServiceComponents(
            plugin_runtime_candidate_resolver=_UnusedPluginRuntimeCandidateResolver(),
            plugin_runtime_staging_authority=_UnusedPluginRuntimeStagingAuthority(),
        ),
    )

    with pytest.raises(RuntimeError, match="configured outside runner mode"):
        async with app.router.lifespan_context(app):
            pytest.fail("lifespan unexpectedly started")


@pytest.mark.anyio
async def test_lifespan_wires_durable_plugin_runtime_coordinator(tmp_path: Path) -> None:
    app = create_app(
        local_settings(
            tmp_path,
            role=ServiceRole.control,
            plugin_runtime_mode="runner",
            plugin_runtime_command_poll_interval_seconds=0.01,
            plugin_runtime_command_lease_seconds=4,
        ),
        components=ServiceComponents(
            plugin_runtime_candidate_resolver=_UnusedPluginRuntimeCandidateResolver(),
            plugin_runtime_staging_authority=_UnusedPluginRuntimeStagingAuthority(),
        ),
    )

    async with app.router.lifespan_context(app):
        assert isinstance(app.state.plugin_runtime_command_coordinator, PluginRuntimeCommandCoordinator)
        assert app.state.plugin_service is not None


@pytest.mark.anyio
async def test_lifespan_builds_default_plugin_runtime_candidate_resolver(tmp_path: Path) -> None:
    app = create_app(
        local_settings(
            tmp_path,
            role=ServiceRole.control,
            plugin_runtime_mode="runner",
            plugin_runtime_command_poll_interval_seconds=0.01,
            plugin_runtime_command_lease_seconds=4,
            plugin_runtime_default_index_url="https://user:index-secret@packages.example/simple",
        ),
        components=ServiceComponents(
            plugin_runtime_staging_authority=_UnusedPluginRuntimeStagingAuthority(),
        ),
    )

    async with app.router.lifespan_context(app):
        assert isinstance(app.state.plugin_runtime_candidate_resolver, FoundationPluginRuntimeCandidateResolver)
        assert isinstance(app.state.plugin_runtime_command_coordinator, PluginRuntimeCommandCoordinator)


@pytest.mark.anyio
async def test_all_in_one_runner_mode_uses_local_supervisor_as_staging_authority(tmp_path: Path) -> None:
    app = create_app(
        local_settings(
            tmp_path,
            plugin_runtime_mode="runner",
            plugin_runtime_command_poll_interval_seconds=0.01,
            plugin_runtime_command_lease_seconds=4,
        )
    )

    async with app.router.lifespan_context(app):
        assert isinstance(app.state.plugin_runner_supervisor, PluginRunnerSupervisor)
        assert isinstance(app.state.plugin_runtime_candidate_resolver, FoundationPluginRuntimeCandidateResolver)
        assert isinstance(app.state.plugin_runtime_command_coordinator, PluginRuntimeCommandCoordinator)


@pytest.mark.anyio
async def test_worker_runner_mode_owns_supervisor_without_control_coordinator(tmp_path: Path) -> None:
    app = create_app(local_settings(tmp_path, role=ServiceRole.worker, plugin_runtime_mode="runner"))

    async with app.router.lifespan_context(app):
        assert isinstance(app.state.plugin_runtime_materializer, PluginRuntimeMaterializer)
        assert isinstance(app.state.plugin_runner_supervisor, PluginRunnerSupervisor)
        assert not hasattr(app.state, "plugin_on_demand_runtime")
        assert not hasattr(app.state, "plugin_runtime_command_coordinator")
        assert not hasattr(app.state, "plugin_runtime_candidate_resolver")


@pytest.mark.anyio
async def test_lifespan_wires_skill_components_only_to_their_process_roles(tmp_path: Path) -> None:
    control = create_app(local_settings(tmp_path / "control", role=ServiceRole.control))
    async with control.router.lifespan_context(control):
        assert not hasattr(control.state, "skill_runtime_preparer")

    worker = create_app(local_settings(tmp_path / "worker", role=ServiceRole.worker))
    async with worker.router.lifespan_context(worker):
        assert isinstance(worker.state.skill_runtime_preparer, SkillRuntimePreparer)
        assert not hasattr(worker.state, "trace_query_service")


@pytest.mark.anyio
async def test_trace_query_client_is_created_only_for_control_plane_roles(tmp_path: Path) -> None:
    query_values = {
        "observability_query_provider": "langfuse",
        "observability_query_langfuse_base_url": "https://langfuse.example.com",
        "observability_query_langfuse_public_key": "pk-test",
        "observability_query_langfuse_secret_key": "sk-test",
    }
    control = create_app(local_settings(tmp_path / "control", role=ServiceRole.control, **query_values))
    worker = create_app(local_settings(tmp_path / "worker", role=ServiceRole.worker, **query_values))

    async with control.router.lifespan_context(control):
        assert control.state.trace_query_service is not None
    async with worker.router.lifespan_context(worker):
        assert not hasattr(worker.state, "trace_query_service")


@pytest.mark.anyio
async def test_distribution_registered_trace_query_provider_is_selected_only_by_control(tmp_path: Path) -> None:
    class Provider:
        capabilities = TraceQueryCapabilities()

    created: list[Provider] = []
    registry = TraceQueryProviderRegistry()

    def create_provider() -> Provider:
        provider = Provider()
        created.append(provider)
        return provider

    registry.register("custom", create_provider)  # type: ignore[arg-type]
    components = ServiceComponents(trace_query_provider_registry=registry)
    control = create_app(
        local_settings(
            tmp_path / "control-custom",
            role=ServiceRole.control,
            observability_query_provider="custom",
        ),
        components=components,
    )
    worker = create_app(
        local_settings(
            tmp_path / "worker-custom",
            role=ServiceRole.worker,
            observability_query_provider="custom",
        ),
        components=components,
    )

    async with control.router.lifespan_context(control):
        assert len(created) == 1
        assert control.state.trace_query_service is not None
    async with worker.router.lifespan_context(worker):
        assert len(created) == 1
        assert not hasattr(worker.state, "trace_query_service")


def test_distribution_cannot_replace_the_builtin_langfuse_provider(tmp_path: Path) -> None:
    registry = TraceQueryProviderRegistry()
    registry.register("langfuse", lambda: object())  # type: ignore[arg-type,return-value]

    with pytest.raises(ValueError, match="already registered"):
        create_app(
            local_settings(tmp_path),
            components=ServiceComponents(trace_query_provider_registry=registry),
        )


@pytest.mark.anyio
async def test_lifespan_fails_closed_without_secret_master_key(tmp_path: Path) -> None:
    app = create_app(
        local_settings(
            tmp_path,
            secret_master_key_base64=None,
            secret_encryption_key_id=None,
        )
    )

    with pytest.raises(SecretProtectionError, match="FOUNDATION_SECRET_MASTER_KEY_BASE64"):
        async with app.router.lifespan_context(app):
            pytest.fail("lifespan unexpectedly started")


@pytest.mark.anyio
async def test_control_lifespan_requires_connectivity_public_origin(tmp_path: Path) -> None:
    app = create_app(
        local_settings(
            tmp_path,
            role=ServiceRole.control,
            connectivity_public_origin=None,
        )
    )

    with pytest.raises(ValueError, match="FOUNDATION_CONNECTIVITY_PUBLIC_ORIGIN"):
        async with app.router.lifespan_context(app):
            pytest.fail("lifespan unexpectedly started")
