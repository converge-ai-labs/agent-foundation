"""Local Composio v3.1 protocol peer; provider data never substitutes Service policy."""

import argparse
import asyncio
import json
import sqlite3
import uuid
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .process import add_listener_arguments, serve_fixture

VERSION = "20260923_00"


def toolkit(version=VERSION, scheme="OAUTH2"):
    return {
        "slug": "github",
        "name": "GitHub",
        "meta": {"version": version, "description": "Local counted GitHub protocol fixture."},
        "auth_schemes": [scheme],
        "composio_managed_auth_schemes": [scheme],
    }


def tool(version=VERSION):
    return {
        "slug": "GITHUB_CREATE_ISSUE",
        "name": "Create issue",
        "description": "Create one counted issue in the local protocol peer.",
        "toolkit": {"slug": "github"},
        "version": version,
        "input_parameters": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
            "additionalProperties": False,
        },
        "output_parameters": {
            "type": "object",
            "properties": {"successful": {"type": "boolean"}, "data": {"type": "object"}},
            "required": ["successful", "data"],
        },
    }


def create_app(database: Path) -> FastAPI:
    app = FastAPI()
    faults = {}
    release_action = asyncio.Event()
    release_completion = asyncio.Event()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE requests (method TEXT, path TEXT, query TEXT)")
        connection.execute("CREATE TABLE accounts (id TEXT PRIMARY KEY, user_id TEXT, auth_config TEXT, status TEXT)")
        connection.execute("CREATE TABLE effects (kind TEXT, account_id TEXT, version TEXT)")

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            if request.headers.get("x-api-key") not in {"fixture-project-key", "rotated-project-key"}:
                return JSONResponse({"error": "invalid_project_key"}, status_code=401)
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "INSERT INTO requests VALUES (?, ?, ?)",
                    (request.method, request.url.path, json.dumps(dict(request.query_params))),
                )
            if faults.get("rate_limited"):
                return JSONResponse({}, status_code=429, headers={"retry-after": "1"})
            if faults.get("oversized"):
                return JSONResponse({"padding": "x" * 300_000})
        return await call_next(request)

    @app.get("/healthz")
    async def health():
        return {"ok": True}

    @app.post("/fixture/control")
    async def control(request: Request):
        faults.clear()
        faults.update(await request.json())
        return {"ok": True}

    @app.get("/fixture/state")
    async def state():
        with sqlite3.connect(database) as connection:
            rows = connection.execute("SELECT method, path, query FROM requests").fetchall()
            effects = connection.execute("SELECT kind, account_id, version FROM effects").fetchall()
        return {
            "requests": [{"method": m, "path": p, "query": json.loads(q)} for m, p, q in rows],
            "effects": [
                {"kind": kind, "account_id": account, "version": version} for kind, account, version in effects
            ],
        }

    @app.get("/api/v3.1/toolkits")
    async def directory():
        return {
            "items": [toolkit(faults.get("current_version", VERSION), faults.get("scheme", "OAUTH2"))],
            "next_cursor": "repeat" if faults.get("cursor_cycle") else None,
            "total_items": 1,
        }

    @app.get("/api/v3.1/toolkits/{slug}")
    async def app_detail(slug: str):
        return (
            toolkit(faults.get("current_version", VERSION), faults.get("scheme", "OAUTH2"))
            if slug == "github"
            else JSONResponse({}, status_code=404)
        )

    @app.get("/api/v3.1/auth_configs")
    async def auth_configs():
        return {
            "items": [
                {
                    "id": "ac_fixture",
                    "name": "Fixture OAuth",
                    "toolkit": {"slug": "github"},
                    "status": "ENABLED",
                    "auth_scheme": faults.get("scheme", "OAUTH2"),
                    "is_composio_managed": True,
                }
            ],
            "next_cursor": None,
            "total_items": 1,
        }

    @app.get("/api/v3.1/tools")
    async def tools(request: Request):
        if request.query_params.get("toolkit_slug") != "github":
            return JSONResponse({}, status_code=400)
        version = request.query_params.get("toolkit_versions[github]")
        if version not in {VERSION, "20260924_00"} or faults.get("unavailable_version") == version:
            return JSONResponse({}, status_code=400)
        value = tool(version)
        if faults.get("wrong_tool_version"):
            value["version"] = "20260922_00"
        if faults.get("sparse_pages"):
            if request.query_params.get("cursor") == "second":
                value["slug"] = "GITHUB_GET_ISSUE"
                return {"items": [value], "next_cursor": None, "total_items": 2}
            return {"items": [{"slug": value["slug"]}], "next_cursor": "second", "total_items": 2}
        return {"items": [value], "next_cursor": None, "total_items": 1}

    @app.get("/api/v3.1/tools/{slug}")
    async def tool_detail(slug: str, request: Request):
        version = request.query_params.get("version")
        if version not in {VERSION, "20260924_00"} or faults.get("unavailable_version") == version:
            return JSONResponse({}, status_code=400)
        value = tool(version)
        value["slug"] = slug
        if faults.get("wrong_tool_version"):
            value["version"] = "20260922_00"
        return value

    @app.post("/api/v3.1/connected_accounts/link")
    async def link(request: Request):
        body = await request.json()
        if body["auth_config_id"] != "ac_fixture":
            return JSONResponse({}, status_code=400)
        account_id = "ca_" + uuid.uuid4().hex
        with sqlite3.connect(database) as connection:
            connection.execute(
                "INSERT INTO accounts VALUES (?, ?, ?, ?)",
                (account_id, body["user_id"], body["auth_config_id"], "PENDING"),
            )
            connection.execute("INSERT INTO effects VALUES (?, ?, ?)", ("setup", account_id, None))
        if faults.get("drop_setup"):
            return JSONResponse({}, status_code=500)
        return {
            "connected_account_id": account_id,
            "redirect_url": str(request.base_url).rstrip("/") + "/connect/" + account_id,
            "expires_at": "2099-01-01T00:00:00Z",
        }

    @app.get("/api/v3.1/connected_accounts/{account_id}")
    async def inspect_account(account_id: str):
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT user_id, auth_config, status FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
        if row is None:
            return JSONResponse({}, status_code=404)
        if faults.get("drop_inspect_after_complete") and row[2] == "ACTIVE":
            return JSONResponse({}, status_code=503)
        return {
            "id": account_id,
            "user_id": "usr_wrong" if faults.get("wrong_owner") else row[0],
            "toolkit": {"slug": "github"},
            "status": row[2],
            "auth_config": {
                "id": "ac_wrong"
                if faults.get("wrong_auth_config") or (faults.get("wrong_config_after_complete") and row[2] == "ACTIVE")
                else row[1],
                "auth_scheme": faults.get("scheme", "OAUTH2"),
                "is_disabled": faults.get("disabled_auth_config", False),
            },
            "state": {"access_token": "fixture-private-token"},
        }

    @app.post("/api/v3.1/connected_accounts/complete_auth")
    async def complete_account(request: Request):
        body = await request.json()
        if faults.get("reject_complete"):
            return JSONResponse({}, status_code=400)
        account_id = body["session_uri"].removeprefix("fixture://")
        with sqlite3.connect(database) as connection:
            row = connection.execute("SELECT user_id, status FROM accounts WHERE id = ?", (account_id,)).fetchone()
            if row is None or row[0] != body["user_id"] or row[1] != "PENDING":
                return JSONResponse({}, status_code=404)
            connection.execute("UPDATE accounts SET status = 'ACTIVE' WHERE id = ?", (account_id,))
            connection.execute("INSERT INTO effects VALUES (?, ?, ?)", ("complete", account_id, None))
        if faults.get("hold_complete"):
            await release_completion.wait()
        if faults.get("drop_complete"):
            return JSONResponse({}, status_code=500)
        return {
            "connected_account_id": "ca_wrong" if faults.get("wrong_completion_account") else account_id,
            "toolkit_slug": "github",
            "status": "ACTIVE",
        }

    @app.post("/api/v3.1/connected_accounts/{account_id}/revoke")
    async def revoke_account(account_id: str):
        if faults.get("unsupported_revoke"):
            return JSONResponse({}, status_code=400)
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE accounts SET status = 'REVOKED' WHERE id = ?", (account_id,))
            connection.execute("INSERT INTO effects VALUES (?, ?, ?)", ("revoke", account_id, None))
        if faults.get("drop_revoke"):
            return JSONResponse({}, status_code=500)
        return {"status": "REVOKED"}

    @app.post("/fixture/release-completion")
    async def release_completed():
        release_completion.set()
        return {"ok": True}

    @app.post("/fixture/activate/{account_id}")
    async def activate(account_id: str):
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE accounts SET status = 'ACTIVE' WHERE id = ?", (account_id,))
        return {"ok": True}

    @app.get("/connect/{account_id}")
    async def connect_page(account_id: str):
        with sqlite3.connect(database) as connection:
            exists = connection.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if exists is None:
            return JSONResponse({}, status_code=404)
        return HTMLResponse(
            "<!doctype html><html><head><meta name='referrer' content='no-referrer'><title>Connect GitHub</title></head><body><h1>Connect GitHub</h1><p>Authorize your personal account for this workspace.</p><form method='post'><button>Authorize GitHub</button></form></body></html>"
        )

    @app.post("/connect/{account_id}")
    async def connect_consent(account_id: str):
        with sqlite3.connect(database) as connection:
            exists = connection.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone()
        verifier = faults.get("verifier_url")
        if exists is None or not verifier:
            return JSONResponse({}, status_code=400)
        if faults.get("scheme", "OAUTH2") != "OAUTH2":
            with sqlite3.connect(database) as connection:
                connection.execute("UPDATE accounts SET status = 'ACTIVE' WHERE id = ?", (account_id,))
            return RedirectResponse(
                verifier + "?" + urlencode({"status": "success", "connected_account_id": account_id}),
                status_code=303,
            )
        return RedirectResponse(verifier + "?" + urlencode({"session_uri": "fixture://" + account_id}), status_code=303)

    @app.post("/api/v3.1/tools/execute/{slug}")
    async def execute(slug: str, request: Request):
        body = await request.json()
        if body["version"] != VERSION:
            return JSONResponse({}, status_code=400)
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT user_id, status FROM accounts WHERE id = ?", (body["connected_account_id"],)
            ).fetchone()
            if row is None or row != (body["user_id"], "ACTIVE"):
                return JSONResponse({}, status_code=403)
            connection.execute(
                "INSERT INTO effects VALUES (?, ?, ?)", ("action", body["connected_account_id"], body["version"])
            )
        if faults.get("hold_action"):
            await release_action.wait()
        return {"successful": True, "data": {"version": body["version"], "slug": slug}}

    @app.post("/fixture/release-action")
    async def release():
        release_action.set()
        return {"ok": True}

    return app


def main():
    parser = argparse.ArgumentParser()
    add_listener_arguments(parser)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    serve_fixture(create_app(args.database), fd=args.fd, shared_socket=args.shared_socket)


if __name__ == "__main__":
    main()
