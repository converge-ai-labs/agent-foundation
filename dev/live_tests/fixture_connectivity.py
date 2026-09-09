"""Local HTTP peers for the production MCP client and Composio adapter.

These peers replace external providers, not Service management or Harness tools.
They retain the actual dispatch arguments and credential hashes as test evidence.
"""

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta

import anyio
from fastapi import APIRouter, HTTPException, Request, Response

TOOL_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}
TOOL_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "successful": {"type": "boolean"},
        "data": {
            "type": "object",
            "properties": {"proof": {"type": "string"}},
            "required": ["proof"],
        },
    },
    "required": ["successful", "data"],
}
TOOLKIT_VERSION = "20260908_01"


def connectivity_router(root, config):
    router = APIRouter()
    account_path = anyio.Path(root / "connector-account.json")

    async def observe(request, body, kind):
        supplied = (
            request.headers.get("x-api-key", "") if kind == "connector" else request.headers.get("authorization", "")
        )
        expected = config["token"] if kind == "connector" else "Bearer " + config["token"]
        if not secrets.compare_digest(supplied, expected):
            raise HTTPException(401, "Test peer credential required")
        value = {
            "path": request.url.path,
            "body": body,
            "credential_sha256": hashlib.sha256(supplied.encode()).hexdigest(),
        }
        async with await anyio.Path(root / (kind + "-requests.jsonl")).open("a") as output:
            await output.write(json.dumps(value) + "\n")

    @router.post("/__live__/mcp")
    async def mcp(request: Request):
        body = await request.json()
        await observe(request, body, "mcp")
        method = body["method"]
        if "id" not in body:
            return Response(status_code=202)
        if method == "initialize":
            result = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "Live MCP peer", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {"name": name, "description": "Return a live HTTP proof", "inputSchema": TOOL_SCHEMA}
                    for name in ("live_echo", "live_forbidden")
                ]
            }
        elif method == "tools/call":
            assert body["params"]["name"] in {"live_echo", "live_forbidden"}
            result = {
                "content": [{"type": "text", "text": "REMOTE:" + body["params"]["arguments"]["value"]}],
                "isError": False,
            }
        elif method == "ping":
            result = {}
        else:
            return {"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32601, "message": "Unsupported method"}}
        return {"jsonrpc": "2.0", "id": body["id"], "result": result}

    @router.get("/__live__/mcp")
    async def mcp_events():
        return Response(status_code=405)  # Stateless JSON responses; no server notification channel.

    @router.api_route("/api/v3.1/{path:path}", methods=["GET", "POST"])
    async def composio(path: str, request: Request):
        body = await request.json() if request.method == "POST" else {}
        await observe(request, body, "connector")
        toolkit = {
            "slug": "live",
            "name": "Live toolkit",
            "meta": {"version": TOOLKIT_VERSION, "description": "Local HTTP proof"},
        }
        if path == "toolkits":
            return {"items": [toolkit], "next_cursor": None}
        if path == "toolkits/live":
            return toolkit
        if path == "auth_configs":
            return {
                "items": [
                    {"id": "live-auth", "status": "ENABLED", "auth_scheme": "OAUTH2", "toolkit": {"slug": "live"}}
                ],
                "next_cursor": None,
            }
        if path == "connected_accounts":
            return {"items": [], "next_cursor": None}
        if path == "connected_accounts/link":
            await account_path.write_text(
                json.dumps(
                    {
                        "id": "live-account",
                        "toolkit": {"slug": "live"},
                        "user_id": body["user_id"],
                        "status": "INITIALIZING",
                    }
                )
            )
            return {
                "connected_account_id": "live-account",
                "redirect_url": "https://connect.composio.dev/link/live-test",
                "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            }
        if path == "connected_accounts/complete_auth":
            account = json.loads(await account_path.read_text())
            assert body == {"session_uri": "live-session", "user_id": account["user_id"]}
            assert account["status"] == "INITIALIZING"
            await account_path.write_text(json.dumps({**account, "status": "ACTIVE"}))
            return {"connected_account_id": account["id"], "toolkit_slug": "live"}
        if path == "connected_accounts/live-account":
            return json.loads(await account_path.read_text())
        if path == "connected_accounts/live-account/revoke":
            value = json.loads(await account_path.read_text())
            await account_path.write_text(json.dumps({**value, "status": "REVOKED"}))
            return {}
        if path == "tools":
            return {"items": [{"slug": key} for key in ("live_echo", "live_forbidden")], "next_cursor": None}
        if path in {"tools/live_echo", "tools/live_forbidden"}:
            return {
                "slug": path.split("/")[-1],
                "toolkit": {"slug": "live"},
                "version": TOOLKIT_VERSION,
                "description": "Return a real HTTP proof",
                "input_parameters": TOOL_SCHEMA,
                "output_parameters": TOOL_OUTPUT_SCHEMA,
            }
        if path.startswith("tools/execute/"):
            account = json.loads(await account_path.read_text())
            if account["status"] != "ACTIVE":
                raise HTTPException(403, "Account revoked")
            assert body["connected_account_id"] == "live-account" and body["version"] == TOOLKIT_VERSION
            assert body["user_id"] == account["user_id"]
            return {"successful": True, "data": {"proof": "REMOTE:" + body["arguments"]["value"]}}
        raise HTTPException(404, "Unknown test provider endpoint")

    return router
