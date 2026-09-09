import pytest
from a13n_service import __version__
from a13n_service.app import create_app
from a13n_service.settings import ProcessRole, Settings

from .support import request


def test_app_exposes_settings_before_lifespan() -> None:
    settings = Settings(service={"role": ProcessRole.worker})

    app = create_app(settings)

    assert app.state.settings is settings


def test_health_reports_process_role() -> None:
    response = request(create_app(Settings(service={"role": ProcessRole.worker})), "/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "role": "worker"}


def test_control_plane_openapi_uses_api_namespace() -> None:
    app = create_app(Settings(service={"role": ProcessRole.control, "build_version": "1.2.3"}))

    response = request(app, "/api/openapi.json")

    assert response.status_code == 200
    document = response.json()
    # A deployment build label must not replace the installed package version.
    assert document["info"] == {"title": "a13n Service", "version": __version__}
    assert "/healthz" not in document["paths"]
    assert "/readyz" not in document["paths"]
    schemas = document["components"]["schemas"]
    assert {
        "Account",
        "Asset",
        "AccountTarget",
        "Model",
        "Skill",
        "SkillPackageManifest",
        "SkillRevision",
    } <= schemas.keys()
    assert {"Observation", "TraceCollection", "TraceDetail", "TraceSummary"} <= schemas.keys()
    assert {
        "Ingress",
        "Route",
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
    assert "/api/v1/plugins" not in document["paths"]
    assert "/api/v1/plugins/{plugin_id}/versions" not in document["paths"]
    assert "/api/v1/plugin-versions/{plugin_version_id}" not in document["paths"]
    assert "/api/v1/plugin-versions/{plugin_version_id}/activate" not in document["paths"]
    assert "/api/v1/plugins/{plugin_id}/deactivate" not in document["paths"]
    assert "/api/v1/operations/{operation_id}" not in document["paths"]
    assert "/api/v1/workspaces/{workspace_id}/hook-subscriptions" in document["paths"]
    assert "/api/v1/hook-subscriptions/{subscription_id}" in document["paths"]
    assert "/api/v1/workspaces/{workspace_id}/events" in document["paths"]
    assert "/api/v1/runs/{run_id}/events" in document["paths"]
    assert "/api/v1/run-attempts/{run_attempt_id}/events" in document["paths"]
    assert "/api/v1/runs/{run_id}/stream" in document["paths"]
    assert "/api/v1/workspaces/{workspace_id}/sessions" in document["paths"]
    assert "/api/v1/sessions/{session_id}/threads" in document["paths"]
    assert "/api/v1/threads/{thread_id}" in document["paths"]
    assert "/api/v1/workspaces/{workspace_id}/runs" in document["paths"]
    assert "/api/v1/threads/{thread_id}/runs" in document["paths"]
    assert "/api/v1/runs/{run_id}" in document["paths"]
    assert "/api/v1/runs/{run_id}/lineage" in document["paths"]
    assert "/api/v1/runs/{run_id}/items" in document["paths"]
    assert "/api/v1/runs/{run_id}/pending-actions" in document["paths"]
    assert "/api/v1/runs/{run_id}/attempts" in document["paths"]
    assert "/api/v1/run-attempts/{run_attempt_id}" in document["paths"]
    assert "/ag-ui/v1/agents/{agent_id}/runs" in document["paths"]
    assert "/ag-ui/v1/agents/{agent_id}/cancel" in document["paths"]
    assert "/a2a/v1/agents/{agent_id}/agent-card.json" in document["paths"]
    assert "/a2a/v1/agents/{agent_id}/message:send" in document["paths"]
    assert "/a2a/v1/agents/{agent_id}/message:stream" in document["paths"]
    assert "/a2a/v1/agents/{agent_id}/tasks/{task_id}" in document["paths"]
    assert "/a2a/v1/agents/{agent_id}/tasks/{task_id}:cancel" in document["paths"]
    assert "/a2a/v1/agents/{agent_id}/tasks/{task_id}:subscribe" in document["paths"]
    assert "/a2a/v1/agents/{agent_id}/tasks/{task_id}/pushNotificationConfigs" in document["paths"]
    assert "/a2a/v1/agents/{agent_id}/tasks/{task_id}/pushNotificationConfigs/{config_id}" in document["paths"]
    assert "/api/v1/runs/{run_id}/interrupt" in document["paths"]
    assert "/api/v1/runs/{run_id}/feedback" in document["paths"]
    assert "/api/v1/runs/{run_id}/fork" in document["paths"]
    assert "/api/v1/runs/{run_id}/retry" in document["paths"]
    assert "/api/v1/runs/{run_id}/steer" in document["paths"]
    assert "/api/v1/runs/{run_id}/steers/{steer_id}" in document["paths"]
    assert "/api/v1/threads/{thread_id}/queued-submissions" in document["paths"]
    assert "/api/v1/threads/{thread_id}/queued-submissions/consume" in document["paths"]
    assert "/api/v1/queued-submissions/{queued_submission_id}" in document["paths"]
    assert "/api/v1/threads/{thread_id}/queued-submissions/reorder" in document["paths"]
    assert "/api/v1/workspaces/{workspace_id}/application-accounts" in document["paths"]
    assert "/api/v1/application-accounts/{account_id}/credentials" in document["paths"]
    assert "/api/v1/ingresses/{ingress_id}/credentials" not in document["paths"]
    assert "/api/v1/workspaces/{workspace_id}/ingresses" not in document["paths"]
    assert "/api/v1/application-accounts/{account_id}/targets" in document["paths"]
    assert "/api/v1/ingresses/{ingress_id}/routes" not in document["paths"]
    assert "/api/v1/application-accounts/{account_id}/targets/{target_id}" in document["paths"]
    assert "/api/v1/workspaces/{workspace_id}/mcp-connections" in document["paths"]
    assert "/api/v1/mcp-connections/{connection_id}/authorize" in document["paths"]
    assert "/api/v1/oauth/mcp/client-metadata.json" in document["paths"]
    connectivity_paths = {
        path: operations
        for path, operations in document["paths"].items()
        if any(
            segment in path
            for segment in ("/application-accounts", "/ingresses", "/connectors", "/connector-connections", "/mcp")
        )
    }
    assert connectivity_paths
    assert all(
        operation.get("tags") == ["connectivity-management"]
        for operations in connectivity_paths.values()
        for operation in operations.values()
        if isinstance(operation, dict)
    )
    for name in ("CreateAccountRequest", "ReplaceAccountCredentialsRequest"):
        assert schemas[name]["properties"]["credentials"]["writeOnly"]
    assert {"target_kind", "external_target_id"} <= set(schemas["TargetConfig"]["required"])
    assert "CreateIngressRequest" not in schemas
    for name in ("Account", "AccountTarget", "TargetConfig"):
        assert "credentials" not in schemas[name]["properties"]


@pytest.mark.parametrize("role", [ProcessRole.control, ProcessRole.all])
def test_control_roles_do_not_serve_browser_routes(role: ProcessRole) -> None:
    app = create_app(Settings(service={"role": role}))

    for path in ("/", "/executions/example", "/assets/app.js"):
        response = request(app, path)
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")


def test_unknown_api_paths_return_json_errors() -> None:
    app = create_app(Settings(service={"role": ProcessRole.all}))

    for path in ("/api", "/api/unknown"):
        response = request(app, path)
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == {"detail": "API route not found"}


def test_worker_role_serves_only_operational_endpoints() -> None:
    app = create_app(Settings(service={"role": ProcessRole.worker}))

    assert request(app, "/healthz").status_code == 200
    assert request(app, "/api/openapi.json").status_code == 404
    assert request(app, "/").status_code == 404


def test_a2a_switch_removes_discovery_and_runtime_routes() -> None:
    app = create_app(Settings(service={"role": ProcessRole.control}, gateway={"a2a_enabled": False}))

    document = request(app, "/api/openapi.json").json()
    assert all(not path.startswith("/a2a/") for path in document["paths"])
    assert "/.well-known/agent-card.json" not in document["paths"]


def test_connectivity_role_exposes_no_control_plane_routes() -> None:
    app = create_app(Settings(service={"role": ProcessRole.connectivity}))

    assert request(app, "/healthz").json() == {"status": "ok", "role": "connectivity"}
    assert request(app, "/api/openapi.json").status_code == 404
    assert request(app, "/").status_code == 404
    assert request(app, "/connectivity/v1/accounts/acct_test/events", method="POST").status_code == 503


def test_non_connectivity_roles_do_not_expose_provider_data_plane() -> None:
    for role in (ProcessRole.control, ProcessRole.worker):
        app = create_app(Settings(service={"role": role}))
        assert request(app, "/connectivity/v1/accounts/acct_test/events", method="POST").status_code == 404
