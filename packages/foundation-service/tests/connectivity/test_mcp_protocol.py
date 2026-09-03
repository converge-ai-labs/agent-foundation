from __future__ import annotations

import json

import httpx2
import pytest
from a13n_service.connectivity.mcp.catalog import catalog_bytes
from a13n_service.connectivity.mcp.credentials import (
    MCPCredentialError,
    bearer_bundle,
    decode_request_headers,
    normalize_static_header_names,
    static_header_bundle,
)
from a13n_service.connectivity.mcp.domain import MCP_PROTOCOL_REVISION
from a13n_service.connectivity.mcp.protocol import MCPProtocolClient, MCPProtocolError
from a13n_service.connectivity.outbound_policy import EndpointPolicy
from pydantic import SecretStr


def response(payload: dict[str, object], *, session_id: str | None = None) -> httpx2.Response:
    headers = {"content-type": "application/json"}
    if session_id is not None:
        headers["mcp-session-id"] = session_id
    return httpx2.Response(200, headers=headers, json=payload)


@pytest.mark.anyio
async def test_discovers_paginated_json_catalog_with_session_protocol_headers() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.method == "DELETE":
            return httpx2.Response(204)
        body = json.loads(request.content)
        if body["method"] == "initialize":
            initialized = response(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": MCP_PROTOCOL_REVISION,
                        "serverInfo": {"name": "example", "version": "1.2.3"},
                        "capabilities": {"tools": {"listChanged": True}},
                    },
                },
                session_id="session-1",
            )
            initialized.headers["set-cookie"] = "remote-cookie=forbidden"
            return initialized
        if body["method"] == "notifications/initialized":
            assert "cookie" not in request.headers
            return httpx2.Response(202)
        cursor = body["params"].get("cursor")
        tools = (
            [{"name": "first", "inputSchema": {"type": "object"}}]
            if cursor is None
            else [{"name": "second", "description": "two", "inputSchema": {"type": "object"}}]
        )
        result: dict[str, object] = {"tools": tools}
        if cursor is None:
            result["nextCursor"] = "next"
        return response({"jsonrpc": "2.0", "id": body["id"], "result": result})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle), follow_redirects=False) as client:
        discovery = await MCPProtocolClient(client, EndpointPolicy()).discover(
            "https://8.8.8.8/mcp",
            credential_headers={"Authorization": "Bearer secret"},
        )

    assert [tool.name for tool in discovery.tools] == ["first", "second"]
    assert requests[0].headers.get("mcp-protocol-version") is None
    assert all(request.headers["mcp-protocol-version"] == MCP_PROTOCOL_REVISION for request in requests[1:])
    assert all(request.headers["mcp-session-id"] == "session-1" for request in requests[1:])
    assert requests[-1].method == "DELETE"
    body = await catalog_bytes(connection_id="mcpc_one", credential_generation=3, discovery=discovery)
    decoded = json.loads(body)
    assert decoded["source_kind"] == "mcp_connection"
    assert decoded["credential_generation"] == 3


@pytest.mark.anyio
async def test_accepts_bounded_sse_and_rejects_unknown_response_id() -> None:
    responses = iter(
        [
            httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    b"event: message\n"
                    b'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
                    b'data: {"jsonrpc":"2.0","id":99,"result":{}}\n\n'
                ),
            )
        ]
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: next(responses))) as client:
        with pytest.raises(MCPProtocolError, match="invalid_response_id"):
            await MCPProtocolClient(client, EndpointPolicy()).discover("https://8.8.8.8/mcp")


@pytest.mark.anyio
async def test_catalog_pages_share_one_total_response_budget() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        if body["method"] == "initialize":
            return response(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": MCP_PROTOCOL_REVISION,
                        "serverInfo": {"name": "example", "version": "1"},
                        "capabilities": {},
                    },
                }
            )
        if body["method"] == "notifications/initialized":
            return httpx2.Response(202)
        return response(
            {
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "tools": [
                        {
                            "name": "large",
                            "description": "x" * 300,
                            "inputSchema": {"type": "object"},
                        }
                    ]
                },
            }
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        with pytest.raises(MCPProtocolError, match="response_too_large"):
            await MCPProtocolClient(client, EndpointPolicy(), response_max_bytes=400).discover("https://8.8.8.8/mcp")


@pytest.mark.anyio
async def test_origin_redirect_is_rejected_before_forwarding_session_or_credentials() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx2.Response(307, headers={"location": "https://8.8.4.4/mcp"})
        raise AssertionError("cross-origin request must not be sent")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle), follow_redirects=False) as client:
        with pytest.raises(MCPProtocolError, match="origin_change_redirect"):
            await MCPProtocolClient(client, EndpointPolicy()).discover(
                "https://8.8.8.8/mcp",
                credential_headers={"Authorization": "Bearer secret"},
            )
    assert requests[0].headers["authorization"] == "Bearer secret"
    assert len(requests) == 1


def test_static_header_and_bearer_credentials_are_validated_as_complete_bundles() -> None:
    names = normalize_static_header_names(("X-API-Key", "X-Tenant"))
    bundle = static_header_bundle(
        {"x-api-key": SecretStr("secret"), "X-Tenant": SecretStr("tenant")},
        expected_names=names,
    )
    assert decode_request_headers(bundle) == {"x-api-key": "secret", "x-tenant": "tenant"}
    assert decode_request_headers(bearer_bundle(SecretStr("token"))) == {"Authorization": "Bearer token"}
    with pytest.raises(MCPCredentialError, match="reserved"):
        normalize_static_header_names(("Authorization",))
    with pytest.raises(MCPCredentialError, match="match"):
        static_header_bundle({"x-api-key": SecretStr("secret")}, expected_names=names)
