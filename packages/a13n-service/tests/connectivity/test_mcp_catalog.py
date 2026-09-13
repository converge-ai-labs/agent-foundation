from __future__ import annotations

import pytest
from a13n_service.configuration.sections import MCPServerSettings
from a13n_service.connectivity.mcp.catalog import MCPServerCatalog
from a13n_service.connectivity.mcp.errors import MCPConnectionError
from a13n_service.endpoint_policy import EndpointPolicy


def _server(**changes: object) -> MCPServerSettings:
    values = {
        "key": "deployment-server",
        "name": "Deployment Server",
        "description": "A deployment-owned catalog entry",
        "endpoint_url": "https://mcp.example/mcp",
        "auth_mode": "oauth",
        **changes,
    }
    return MCPServerSettings.model_validate(values)


def test_catalog_merges_deployment_entries_and_paginates_stably() -> None:
    catalog = MCPServerCatalog((_server(),), EndpointPolicy(require_https=True))

    first = catalog.list(query="deployment-owned catalog entry", limit=200, cursor=None)
    assert [item.key for item in first.items] == ["deployment-server"]
    assert first.items[0].origin == "deployment"
    assert first.next_cursor is None

    page = catalog.list(query="", limit=1, cursor=None)
    assert page.next_cursor is not None
    following = catalog.list(query="", limit=1, cursor=page.next_cursor)
    assert following.items[0].key > page.items[0].key

    with pytest.raises(MCPConnectionError, match="cursor"):
        catalog.list(query="different", limit=1, cursor=page.next_cursor)


def test_catalog_requires_explicit_builtin_override() -> None:
    with pytest.raises(ValueError, match="override_builtin"):
        MCPServerCatalog((_server(key="airtable"),), EndpointPolicy())

    catalog = MCPServerCatalog((_server(key="airtable", override_builtin=True),), EndpointPolicy())
    assert catalog.get("airtable").origin == "deployment"
    assert catalog.get("airtable").endpoint_url == "https://mcp.example/mcp"


def test_catalog_validates_browser_and_endpoint_urls() -> None:
    with pytest.raises(ValueError, match="Invalid MCP server catalog entry"):
        MCPServerCatalog(
            (_server(endpoint_url="http://mcp.example/mcp"),),
            EndpointPolicy(require_https=True),
        )
    with pytest.raises(ValueError, match="Invalid MCP server catalog entry"):
        MCPServerCatalog((_server(logo_url="javascript:alert(1)"),), EndpointPolicy())


def test_catalog_reports_missing_entries_without_leaking_keys() -> None:
    catalog = MCPServerCatalog((), EndpointPolicy())
    with pytest.raises(MCPConnectionError, match="requested resource was not found"):
        catalog.get("missing")
