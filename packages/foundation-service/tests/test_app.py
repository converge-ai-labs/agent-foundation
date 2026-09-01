import asyncio
from base64 import b64encode
from pathlib import Path

import httpx2
import pytest
from a13n_service.app import ServiceComponents, create_app
from a13n_service.connectors import ConnectorProviderCatalog, LocalConnectorProviderOperations
from a13n_service.secret_management import SecretProtectionError
from a13n_service.settings import ServiceRole, ServiceSettings
from a13n_service.skill_management import AgentSkillLockResolver, FoundationSkillRuntimePreparer
from fastapi import FastAPI


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
        "connector_internal_auth_token": "foundation-service-test-connector-token",
    }
    values.update(updates)
    return ServiceSettings(**values)


def test_health_reports_process_role() -> None:
    response = request(create_app(ServiceSettings(_env_file=None, role=ServiceRole.worker)), "/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "role": "worker"}


def test_control_plane_openapi_uses_api_namespace() -> None:
    app = create_app(ServiceSettings(_env_file=None, role=ServiceRole.control, build_version="1.2.3"))

    response = request(app, "/api/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"] == {"title": "Agent Foundation Service", "version": "1.2.3"}
    assert "/healthz" not in response.json()["paths"]
    assert "/readyz" not in response.json()["paths"]
    assert request(app, "/api/docs").status_code == 200
    assert request(app, "/api/docs/oauth2-redirect").status_code == 200
    assert request(app, "/openapi.json").status_code == 404
    assert request(app, "/docs/oauth2-redirect").status_code == 404


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


def test_connector_role_exposes_no_control_plane_routes() -> None:
    app = create_app(ServiceSettings(_env_file=None, role=ServiceRole.connector))

    assert request(app, "/healthz").json() == {"status": "ok", "role": "connector"}
    assert request(app, "/api/openapi.json").status_code == 404
    assert request(app, "/").status_code == 404


def test_configured_web_build_requires_an_index(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Foundation Web index is missing"):
        create_app(ServiceSettings(_env_file=None, role=ServiceRole.control, web_dist_dir=tmp_path))


@pytest.mark.anyio
async def test_lifespan_constructs_storage_once_and_readiness_uses_it(tmp_path: Path) -> None:
    app = create_app(local_settings(tmp_path))

    async with app.router.lifespan_context(app):
        storage = app.state.storage
        assert len(app.state.connector_providers) == 0
        assert app.state.db_engine is storage.engine
        assert app.state.db_session_factory is storage.sessions
        assert isinstance(app.state.agent_skill_lock_resolver, AgentSkillLockResolver)
        assert isinstance(app.state.skill_runtime_preparer, FoundationSkillRuntimePreparer)

        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/readyz")

        assert response.status_code == 200
        assert response.json() == {"status": "ready", "role": "all"}


@pytest.mark.anyio
async def test_provider_code_is_loaded_only_by_connector_capable_roles(tmp_path: Path) -> None:
    catalog = ConnectorProviderCatalog(())
    control_components = ServiceComponents(connector_provider_operations=LocalConnectorProviderOperations(catalog))
    provider_components = ServiceComponents(connector_provider_catalog=catalog)
    directories = {name: tmp_path / name for name in ("control", "worker", "connector")}
    for directory in directories.values():
        directory.mkdir()
    control = create_app(
        local_settings(directories["control"], role=ServiceRole.control),
        components=control_components,
    )
    worker = create_app(
        local_settings(directories["worker"], role=ServiceRole.worker),
        components=provider_components,
    )
    connector = create_app(
        local_settings(
            directories["connector"],
            role=ServiceRole.connector,
            connector_internal_auth_token="0" * 32,
        ),
        components=provider_components,
    )

    async with control.router.lifespan_context(control):
        assert not hasattr(control.state, "connector_providers")
        assert control.state.connector_provider_operations is not None
        assert not hasattr(control.state, "connector_provider_runtime")
    async with worker.router.lifespan_context(worker):
        assert not hasattr(worker.state, "connector_providers")
        assert not hasattr(worker.state, "connector_provider_runtime")
    async with connector.router.lifespan_context(connector):
        assert connector.state.connector_providers is catalog
        assert connector.state.connector_provider_runtime is not None
        assert connector.state.connector_mcp_gateway is not None


@pytest.mark.anyio
async def test_lifespan_wires_skill_components_only_to_their_process_roles(tmp_path: Path) -> None:
    control = create_app(local_settings(tmp_path / "control", role=ServiceRole.control))
    async with control.router.lifespan_context(control):
        assert isinstance(control.state.agent_skill_lock_resolver, AgentSkillLockResolver)
        assert not hasattr(control.state, "skill_runtime_preparer")

    worker = create_app(local_settings(tmp_path / "worker", role=ServiceRole.worker))
    async with worker.router.lifespan_context(worker):
        assert not hasattr(worker.state, "agent_skill_lock_resolver")
        assert isinstance(worker.state.skill_runtime_preparer, FoundationSkillRuntimePreparer)


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
