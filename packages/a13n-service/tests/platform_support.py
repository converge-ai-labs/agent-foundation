"""Clients for the internal platform tool contract, independent of HTTP operation IDs."""

from typing import Any

import httpx2

from .mcp_support import client as client


async def rpc(http: httpx2.AsyncClient, method: str, **params: Any) -> httpx2.Response:
    return await http.post(
        "/api/v1/platform-tools/", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    )


async def call(http: httpx2.AsyncClient, tool_name: str, /, **arguments: Any) -> dict:
    response = await rpc(http, "tools/call", name=tool_name, arguments=arguments)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert "structuredContent" in result, result
    value = result["structuredContent"]
    assert result.get("isError", False) == ("error" in value)
    return value
