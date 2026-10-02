"""Real HTTP MCP peer with durable counted effects and deterministic result barriers."""

import argparse
import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


def create_app(database: Path) -> FastAPI:
    app = FastAPI()
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS calls (
                number INTEGER PRIMARY KEY, tool TEXT, operation TEXT, request_digest TEXT,
                context TEXT, auth_digest TEXT, destination TEXT, result TEXT, released INTEGER DEFAULT 0, returned INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS barriers (operation TEXT PRIMARY KEY, context TEXT, released INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS retired_destinations (destination TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS effects (id INTEGER PRIMARY KEY, operation TEXT UNIQUE, value INTEGER);
        """)

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/fixture/state")
    async def state():
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            return {
                "barriers": [dict(row) for row in connection.execute("SELECT * FROM barriers")],
                "calls": [dict(row) for row in connection.execute("SELECT * FROM calls ORDER BY number")],
                "effects": connection.execute("SELECT count(*) FROM effects").fetchone()[0],
            }

    @app.post("/fixture/permit-effect")
    async def permit_effect():
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE barriers SET released=1")
        return {"released": True}

    @app.post("/fixture/release")
    async def release():
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE calls SET released=1")
        return {"released": True}

    @app.post("/fixture/retire-destination")
    async def retire_destination(request: Request):
        body = await request.json()
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO retired_destinations VALUES (?)", (body["destination"],))
        return {"retired": True}

    @app.post("/{auth}/mcp")
    async def rpc(auth: str, request: Request):
        destination = request.url.path + ("?" + request.url.query if request.url.query else "")
        with sqlite3.connect(database) as connection:
            if connection.execute("SELECT 1 FROM retired_destinations WHERE destination=?", (destination,)).fetchone():
                return Response(status_code=410)
        credential = request.headers.get("authorization") if auth == "bearer" else request.headers.get("x-api-key")
        if auth not in {"none", "bearer", "headers"} or (
            auth != "none"
            and credential not in {"Bearer fixture-secret", "fixture-secret", "Bearer rotated-secret", "rotated-secret"}
        ):
            return Response(status_code=401)
        body = await request.json()
        method = body.get("method")
        if "id" not in body:
            return Response(status_code=202)
        modern = request.headers.get("mcp-protocol-version") == "2026-07-28" or method == "server/discover"
        if method == "server/discover":
            result = {
                "supportedVersions": ["2026-07-28", "2025-11-25"],
                "capabilities": {"tools": {}},
            }
        elif method == "initialize":
            result = {
                "protocolVersion": body["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "counted-fixture", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": name,
                        "description": description,
                        "inputSchema": {
                            "type": "object",
                            "properties": {"label": {"type": "string"}, "hold": {"type": "boolean"}},
                            "additionalProperties": False,
                        },
                        "annotations": {"readOnlyHint": True, "idempotentHint": True},
                        "_meta": {"a13n.harness.recovery_retry_safe": True},
                    }
                    for name, description in [
                        ("increment", "Perform one unsafe counted external effect."),
                        ("increment_once", "Apply an effect once per enforced operation identity."),
                        ("read_count", "Read the current effect count."),
                        ("oversized", "Return an oversized result."),
                    ]
                ]
            }
        elif method == "tools/call":
            params = body["params"]
            name = params["name"]
            arguments = params.get("arguments", {})
            metadata = params.get("_meta", {}).get("a13n.service", {})
            operation = metadata.get("operation_id")
            if request.headers.get("x-effect-barrier") == "true":
                with sqlite3.connect(database) as connection:
                    connection.execute(
                        "INSERT OR IGNORE INTO barriers(operation,context) VALUES (?,?)",
                        (operation, json.dumps(metadata)),
                    )
                async with asyncio.timeout(30):
                    while True:
                        with sqlite3.connect(database) as connection:
                            released = connection.execute(
                                "SELECT released FROM barriers WHERE operation=?", (operation,)
                            ).fetchone()[0]
                        if released:
                            break
                        await asyncio.sleep(0.025)
            digest = hashlib.sha256(
                json.dumps({"tool": name, "arguments": arguments}, sort_keys=True).encode()
            ).hexdigest()
            with sqlite3.connect(database) as connection:
                connection.execute("BEGIN IMMEDIATE")
                prior = (
                    connection.execute(
                        "SELECT request_digest,result FROM calls WHERE operation=? AND tool='increment_once' ORDER BY number LIMIT 1",
                        (operation,),
                    ).fetchone()
                    if name == "increment_once" and operation
                    else None
                )
                if name == "increment_once" and not operation:
                    return JSONResponse(
                        {
                            "jsonrpc": "2.0",
                            "id": body["id"],
                            "error": {"code": -32602, "message": "Stable operation identity required"},
                        }
                    )
                if prior and prior[0] != digest:
                    return JSONResponse(
                        {
                            "jsonrpc": "2.0",
                            "id": body["id"],
                            "error": {"code": -32602, "message": "Operation intent conflicts"},
                        }
                    )
                if name in {"increment", "increment_once"} and not prior:
                    connection.execute(
                        "INSERT INTO effects(operation,value) VALUES (?,1)",
                        (operation if name == "increment_once" else None,),
                    )
                count = connection.execute("SELECT count(*) FROM effects").fetchone()[0]
                result = (
                    json.loads(prior[1])
                    if prior
                    else {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "count": count,
                                        "label": arguments.get("label"),
                                        "context": request.headers.get("x-conversation"),
                                    }
                                ),
                            }
                        ]
                    }
                )
                if name == "oversized":
                    result = {"content": [{"type": "text", "text": "x" * 300000}]}
                cursor = connection.execute(
                    "INSERT INTO calls(tool,operation,request_digest,context,auth_digest,destination,result,released) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        name,
                        operation,
                        digest,
                        json.dumps(metadata | {"conversation": request.headers.get("x-conversation")}),
                        hashlib.sha256((credential or "").encode()).hexdigest(),
                        destination,
                        json.dumps(result),
                        int(bool(prior) or not arguments.get("hold")),
                    ),
                )
                number = cursor.lastrowid
            async with asyncio.timeout(120):
                while True:
                    with sqlite3.connect(database) as connection:
                        if connection.execute("SELECT released FROM calls WHERE number=?", (number,)).fetchone()[0]:
                            connection.execute("UPDATE calls SET returned=1 WHERE number=?", (number,))
                            break
                    await asyncio.sleep(0.025)
        elif method == "ping":
            result = {}
        else:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32601, "message": "Unsupported method"}}
            )
        if modern:
            if method in {"server/discover", "tools/list"}:
                result.update(cacheScope="private", ttlMs=0)
            result["resultType"] = "complete"
            result["_meta"] = {"serverInfo": {"name": "counted-fixture", "version": "1"}}
        return JSONResponse({"jsonrpc": "2.0", "id": body["id"], "result": result})

    @app.get("/{auth}/mcp")
    async def no_server_stream(auth: str):
        return Response(status_code=405)

    @app.delete("/{auth}/mcp")
    async def close_session(auth: str):
        return Response(status_code=204)

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fd", type=int, required=True)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    uvicorn.run(create_app(args.database), fd=args.fd, log_level="warning", access_log=False)
