"""Connections in each state, reaching the scripted model process's MCP route (`dev/fixtures/api.py`).

`make dev` runs that route with the scripted model, so the ready connection keeps working in the Console, and
entering any token makes the pending one ready. The Composio connection binds no account: authorizing it would
reach Composio with a fictional key.
"""

from __future__ import annotations

from dev.service.api import Api, Json

LOOKUP_TOOL = "lookup_local_review"
FAILING_TOOL = "fail_local_review"


def seed_connections(api: Api, model_url: str, composio: Json) -> dict[str, Json]:
    """Every connection by state: `ready` is tested, so its tools are listed."""
    server = model_url.removesuffix("/v1") + "/mcp"
    ready = api.post(
        "/api/v1/connections", {"type": "mcp", "name": "Release review (local MCP)", "config": {"url": server}}
    )
    api.post(f"/api/v1/connections/{ready['id']}/test")
    connections = {
        "ready": ready,
        "headers": api.post(
            "/api/v1/connections",
            {
                "type": "mcp",
                "name": "Release review (API key header)",
                "auth": "headers",
                "config": {"url": server, "headers": ["x-api-key"]},
                "credential": {"headers": {"x-api-key": "fictional-review-key"}},
            },
        ),
        "pending": api.post(
            "/api/v1/connections",
            {"type": "mcp", "name": "Release review (token required)", "auth": "bearer", "config": {"url": server}},
        ),
    }
    retired = api.post(
        "/api/v1/connections", {"type": "mcp", "name": "Retired review server", "config": {"url": server}}
    )
    connections["disabled"] = api.patch(f"/api/v1/connections/{retired['id']}", retired, {"enabled": False})
    connections["connector"] = api.post(
        "/api/v1/connections",
        {
            "type": "composio",
            "name": "GitHub (fictional account)",
            "auth": "account",
            "connector_provider_id": composio["id"],
            "config": {
                "app": "github",
                "actions": ["GITHUB_CREATE_ISSUE"],
                "setup": {"auth_config_id": "ac_fictional", "toolkit_version": "20250901_00"},
            },
        },
    )
    return connections
