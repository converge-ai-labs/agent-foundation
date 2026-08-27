import asyncio
from pathlib import Path

import httpx2
import pytest
from a13n_service.app import create_app
from a13n_service.settings import ServiceRole, ServiceSettings
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
    }
    values.update(updates)
    return ServiceSettings(**values)


def test_health_reports_process_role() -> None:
    response = request(create_app(ServiceSettings(_env_file=None, role=ServiceRole.execution)), "/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "role": "execution"}


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


def test_execution_role_serves_only_operational_endpoints(tmp_path: Path) -> None:
    web_dist = create_web_dist(tmp_path / "web")
    app = create_app(ServiceSettings(_env_file=None, role=ServiceRole.execution, web_dist_dir=web_dist))

    assert request(app, "/healthz").status_code == 200
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
        assert app.state.db_engine is storage.engine
        assert app.state.db_session_factory is storage.sessions

        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/readyz")

        assert response.status_code == 200
        assert response.json() == {"status": "ready", "role": "all"}
