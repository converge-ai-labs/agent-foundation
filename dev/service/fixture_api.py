"""Loopback-only protocol fixtures; these never contact an upstream service."""

import json

from fastapi import APIRouter, Request, Response

router = APIRouter()


@router.post("/mcp")
async def mcp(request: Request):
    body = await request.json()
    if "id" not in body:
        return Response(status_code=202)
    method = body.get("method")
    if method == "initialize":
        result = {
            "protocolVersion": body["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "public-local-review-fixture", "version": "1.0"},
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": name,
                    "description": description,
                    "inputSchema": {
                        "type": "object",
                        "properties": {"topic": {"type": "string"}},
                        "required": ["topic"],
                    },
                }
                for name, description in (
                    ("lookup_local_review", "Return a fictional local review record."),
                    ("fail_local_review", "Return an intentional local tool error for failure UI."),
                )
            ]
        }
    elif method == "tools/call":
        failed = body["params"]["name"] == "fail_local_review"
        result = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "fictional": True,
                            "topic": body["params"].get("arguments", {}).get("topic"),
                            "result": "Intentional local tool failure"
                            if failed
                            else "LOCAL-REVIEW-42: keyboard and navigation checks passed",
                        }
                    ),
                }
            ],
            "isError": failed,
        }
    elif method == "ping":
        result = {}
    else:
        return {
            "jsonrpc": "2.0",
            "id": body["id"],
            "error": {"code": -32601, "message": "Unsupported local fixture method"},
        }
    return {"jsonrpc": "2.0", "id": body["id"], "result": result}
