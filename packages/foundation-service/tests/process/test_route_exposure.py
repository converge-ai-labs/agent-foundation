from pathlib import Path

import pytest
from a13n_service.app import create_app
from a13n_service.settings import ServiceRole, ServiceSettings

from .support import create_web_dist, request


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
        "Ingress",
        "Model",
        "Plugin",
        "PluginVersion",
        "Skill",
        "SkillPackageManifest",
        "SkillRevision",
        "Route",
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
    assert "/api/v1/workspaces/{workspace_id}/ingresses" in document["paths"]
    assert "/api/v1/ingresses/{ingress_id}/routes" in document["paths"]
    assert "/api/v1/workspaces/{workspace_id}/mcp-connections" in document["paths"]
    assert "/api/v1/mcp-connections/{connection_id}/authorize" in document["paths"]
    assert "/api/v1/oauth/mcp/client-metadata.json" in document["paths"]
    connectivity_paths = {
        path: operations
        for path, operations in document["paths"].items()
        if any(segment in path for segment in ("/ingresses", "/connectors", "/connector-connections", "/mcp"))
    }
    assert connectivity_paths
    assert all(
        operation.get("tags") == ["connectivity-management"]
        for operations in connectivity_paths.values()
        for operation in operations.values()
        if isinstance(operation, dict)
    )
    assert document["components"]["schemas"]["CreateIngressRequest"]["properties"]["credentials"]["writeOnly"]
    assert "credentials" not in document["components"]["schemas"]["Ingress"]["properties"]


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
    assert request(app, "/connectivity/v1/ingresses/ing_test/events", method="POST").status_code == 503


def test_non_connectivity_roles_do_not_expose_provider_data_plane() -> None:
    for role in (ServiceRole.control, ServiceRole.worker):
        app = create_app(ServiceSettings(_env_file=None, role=role))
        assert request(app, "/connectivity/v1/ingresses/ing_test/events", method="POST").status_code == 404


def test_configured_web_build_requires_an_index(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Foundation Web index is missing"):
        create_app(ServiceSettings(_env_file=None, role=ServiceRole.control, web_dist_dir=tmp_path))
