"""MCP clients and assertions shared by management and trace integration tests."""

from types import SimpleNamespace
from typing import Any

import httpx2
from a13n_service.api_tools import tool_name

MCP = "/api/v1/mcp/"
HEADERS = {"accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-06-18"}


async def key(service: SimpleNamespace, workspace_id: str | None = None) -> dict[str, Any]:
    response = await service.client.post(
        "/api/v1/users/me/keys", json={"workspace_id": workspace_id or service.tenant.workspace_id, "name": "MCP"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def client(service: SimpleNamespace, credential: dict[str, Any]) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=service.app),
        base_url=service.runtime.settings.server.public_origin,
        headers={**HEADERS, "authorization": "Bearer " + credential["secret"]},
    )


async def rpc(http: httpx2.AsyncClient, method: str, **params: Any) -> httpx2.Response:
    return await http.post(MCP, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})


async def call(http: httpx2.AsyncClient, service: SimpleNamespace, method: str, path: str, **arguments: Any) -> dict:
    operation = service.app.openapi()["paths"]["/api/v1" + path][method.lower()]
    response = await rpc(http, "tools/call", name=tool_name(operation["operationId"]), arguments=arguments)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert "structuredContent" in result, result
    value = result["structuredContent"]
    assert result.get("isError", False) == (not 200 <= value["status"] < 300)
    return value
