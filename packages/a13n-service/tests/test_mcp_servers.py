"""The Remote MCP servers suggested for new connections: packaged, extended by the deployment, paged by key."""

import httpx2
import pytest
from a13n_service.providers.tools.mcp_catalog import PACKAGED, McpServer, McpServers, list_mcp_servers
from pydantic import TypeAdapter, ValidationError

pytestmark = pytest.mark.anyio


async def test_signed_in_principals_page_and_search_the_packaged_servers(service) -> None:  # type: ignore[no-untyped-def]
    keys: list[str] = []
    cursor = None
    while True:
        params = {"limit": 50} | ({} if cursor is None else {"cursor": cursor})
        response = await service.client.get("/api/v1/mcp-servers", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        keys += [server["key"] for server in page["items"]]
        if (cursor := page["next_cursor"]) is None:
            break
    assert keys == sorted(server.key for server in PACKAGED) and len(keys) > 150

    found = (await service.client.get("/api/v1/mcp-servers", params={"query": "GITHUB"})).json()["items"]
    github = next(server for server in found if server["key"] == "github")
    assert github["auth"] == "bearer" and github["url"].startswith("https://") and github["documentation_url"]
    headers = (await service.client.get("/api/v1/mcp-servers", params={"query": "jentic"})).json()["items"]
    assert [(server["auth"], server["header_names"]) for server in headers] == [("headers", ["x-jentic-api-key"])]

    # A cursor belongs to the query it paged.
    first = (await service.client.get("/api/v1/mcp-servers", params={"limit": 1})).json()
    response = await service.client.get("/api/v1/mcp-servers", params={"cursor": first["next_cursor"], "query": "a"})
    assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_cursor"

    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=service.app), base_url="https://service.test"
    ) as anonymous:
        assert (await anonymous.get("/api/v1/mcp-servers")).status_code == 401


def test_deployment_entries_add_servers_and_replace_packaged_ones() -> None:
    internal = McpServer(
        key="internal-tools", name="Internal", description="Our tools", url="https://tools.example/mcp", auth="none"
    )
    renamed = PACKAGED[0].model_copy(update={"name": "Renamed"})
    page = list_mcp_servers([internal, renamed], query=None, limit=500, cursor=None)
    by_key = {server.key: server for server in page.items}
    assert len(page.items) == len(PACKAGED) + 1 and page.next_cursor is None
    assert by_key["internal-tools"] == internal and by_key[renamed.key].name == "Renamed"
    assert [server.key for server in page.items] == sorted(by_key)


@pytest.mark.parametrize(
    "entry",
    [
        {"url": "javascript:alert(1)"},
        {"documentation_url": "data:text/html,x"},
        {"auth": "headers"},
        {"auth": "bearer", "header_names": ["x-api-key"]},
        {"auth": "headers", "header_names": ["host"]},
        {"auth": "headers", "header_names": ["mcp-session-id"]},
        {"auth": "headers", "header_names": ["x-api-key", "x-api-key"]},
        {"key": "Upper"},
    ],
)
def test_catalogue_entries_are_links_and_consistent_presets(entry: dict) -> None:
    base = {"key": "custom", "name": "Custom", "description": "A server", "url": "https://custom.example/mcp"}
    with pytest.raises(ValidationError):
        McpServer.model_validate({**base, "auth": "none", **entry})


def test_deployment_entries_have_unique_keys() -> None:
    entry = {"key": "custom", "name": "Custom", "description": "A server", "url": "https://c.example", "auth": "none"}
    with pytest.raises(ValidationError, match="unique"):
        TypeAdapter(McpServers).validate_python([entry, entry])


async def test_photon_is_packaged_and_searchable(service) -> None:  # type: ignore[no-untyped-def]
    photon = next(server for server in PACKAGED if server.key == "photon")
    assert photon.name == "Photon"
    assert photon.url == "https://mcp.photon.codes/mcp"
    assert photon.auth == "oauth"
    assert photon.documentation_url == "https://photon.codes/docs/beta/ai-tools/mcp"
    assert photon.logo_url is not None and photon.logo_url.startswith("https://")
    assert "Tool Search" in photon.requirements
    response = await service.client.get("/api/v1/mcp-servers", params={"query": "photon"})
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert any(item["key"] == "photon" and item["url"] == "https://mcp.photon.codes/mcp" for item in items)
