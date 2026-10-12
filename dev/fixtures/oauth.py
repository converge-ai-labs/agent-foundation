"""Counted authorization codes, rotating refresh tokens and client-credentials tokens with durable fault barriers."""

import argparse
import asyncio
import base64
import hashlib
import html
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from dev.fixtures.mcp import create_app as create_mcp

from .process import add_listener_arguments, serve_fixture


def create_app(database: Path) -> FastAPI:
    app = FastAPI()
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS oauth_codes (
                code TEXT PRIMARY KEY, client TEXT, redirect TEXT, challenge TEXT,
                principal TEXT, consumed INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS oauth_families (
                id TEXT PRIMARY KEY, principal TEXT, access TEXT UNIQUE, refresh TEXT UNIQUE,
                expires REAL, generation INTEGER, revoked INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS oauth_requests (
                id INTEGER PRIMARY KEY, kind TEXT, family TEXT, presented_hash TEXT,
                consumed INTEGER, released INTEGER, returned INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS oauth_access (
                id INTEGER PRIMARY KEY, principal TEXT, token_hash TEXT, method TEXT
            );
            CREATE TABLE IF NOT EXISTS oauth_faults (name TEXT PRIMARY KEY, value TEXT);
        """)

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/.well-known/oauth-protected-resource/tools/none/mcp")
    async def resource_metadata(request: Request):
        origin = str(request.base_url).rstrip("/")
        return {
            "resource": origin + "/tools/none/mcp",
            "authorization_servers": [origin],
            "scopes_supported": ["tools"],
        }

    @app.get("/.well-known/oauth-authorization-server")
    @app.get("/.well-known/openid-configuration")
    async def metadata(request: Request):
        origin = str(request.base_url).rstrip("/")
        result = {
            "issuer": origin,
            "authorization_endpoint": origin + "/authorize",
            "token_endpoint": origin + "/token",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_basic", "client_secret_post"],
            "authorization_response_iss_parameter_supported": True,
            "scopes_supported": ["tools"],
        }
        with sqlite3.connect(database) as connection:
            faults = dict(connection.execute("SELECT name,value FROM oauth_faults"))
        if faults.get("omit_grants"):
            result.pop("grant_types_supported")
        if faults.get("omit_auth_methods"):
            result.pop("token_endpoint_auth_methods_supported")
        if faults.get("empty_grants"):
            result["grant_types_supported"] = []
        if faults.get("empty_auth_methods"):
            result["token_endpoint_auth_methods_supported"] = []
        if faults.get("wrong_issuer"):
            result["issuer"] = origin + "/other"
        if faults.get("no_pkce"):
            result.pop("code_challenge_methods_supported")
        return result

    @app.get("/authorize")
    async def authorize(request: Request):
        params = dict(request.query_params)
        if not all(params.get(name) for name in ("state", "redirect_uri", "client_id", "code_challenge")):
            return Response(status_code=400)
        if params.get("response_type") != "code" or params.get("code_challenge_method") != "S256":
            return Response(status_code=400)
        hidden = "".join(
            f'<input type="hidden" name="{html.escape(name)}" value="{html.escape(value)}">'
            for name, value in params.items()
        )
        return HTMLResponse(
            '<h1>Authorize counted tools</h1><form method="get" action="/consent">'
            + hidden
            + '<label>Remote account <input name="principal" value="alice" required></label>'
            + '<button name="decision" value="allow">Authorize</button>'
            + '<button name="decision" value="deny">Deny</button></form>'
        )

    @app.get("/consent")
    async def consent(request: Request):
        params = request.query_params
        result = {"state": params["state"], "iss": str(request.base_url).rstrip("/")}
        if params.get("decision") == "deny":
            result["error"] = "access_denied"
        else:
            code = secrets.token_urlsafe(24)
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "INSERT INTO oauth_codes(code,client,redirect,challenge,principal) VALUES (?,?,?,?,?)",
                    (code, params["client_id"], params["redirect_uri"], params["code_challenge"], params["principal"]),
                )
            result["code"] = code
        return RedirectResponse(params["redirect_uri"] + "?" + urlencode(result), status_code=302)

    @app.post("/token")
    async def token(request: Request):
        values = {key: value[0] for key, value in parse_qs((await request.body()).decode()).items()}
        kind = values.get("grant_type")
        presented = values.get("code" if kind == "authorization_code" else "refresh_token", "")
        client_id = values.get("client_id")
        auth = request.headers.get("authorization", "")
        # A client that presented the fixture secret; only such a client may use `client_credentials`.
        confidential = auth.startswith("Basic ") or values.get("client_secret") is not None
        if auth.startswith("Basic "):
            from urllib.parse import unquote

            client_id, secret = (unquote(value) for value in base64.b64decode(auth[6:]).decode().split(":", 1))
            if secret != "fixture-client-secret":
                return JSONResponse({"error": "invalid_client"}, status_code=401)
        elif values.get("client_secret") not in (None, "fixture-client-secret"):
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        with sqlite3.connect(database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            faults = dict(connection.execute("SELECT name,value FROM oauth_faults"))
            family = None
            principal = None
            generation = 0
            if kind == "authorization_code":
                code = connection.execute(
                    "SELECT client,redirect,challenge,principal,consumed FROM oauth_codes WHERE code=?", (presented,)
                ).fetchone()
                challenge = (
                    base64.urlsafe_b64encode(hashlib.sha256(values.get("code_verifier", "").encode()).digest())
                    .decode()
                    .rstrip("=")
                )
                if code and not code[4] and code[:3] == (client_id, values.get("redirect_uri"), challenge):
                    connection.execute("UPDATE oauth_codes SET consumed=1 WHERE code=?", (presented,))
                    family, principal = secrets.token_hex(12), code[3]
            elif kind == "refresh_token":
                row = connection.execute(
                    "SELECT id,principal,generation,revoked FROM oauth_families WHERE refresh=?", (presented,)
                ).fetchone()
                if row and not row[3]:
                    family, principal, generation = row[:3]
            elif kind == "client_credentials" and confidential:
                # The machine account is the client itself.
                family, principal = secrets.token_hex(12), client_id
            if family is None:
                if kind == "refresh_token":
                    reused = connection.execute(
                        "SELECT family FROM oauth_requests WHERE presented_hash=? AND consumed=1 AND kind='refresh_token'",
                        (hashlib.sha256(presented.encode()).hexdigest(),),
                    ).fetchone()
                    if reused:
                        connection.execute("UPDATE oauth_families SET revoked=1 WHERE id=?", (reused[0],))
                connection.execute(
                    "INSERT INTO oauth_requests(kind,presented_hash,consumed,released,returned) VALUES (?,?,0,1,1)",
                    (kind, hashlib.sha256(presented.encode()).hexdigest()),
                )
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            expires = int(faults.get("expires_in", "3600"))
            connection.execute(
                "INSERT INTO oauth_families(id,principal,access,refresh,expires,generation) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET access=excluded.access,refresh=excluded.refresh,expires=excluded.expires,generation=excluded.generation",
                (family, principal, access, refresh, time.time() + expires, generation + 1),
            )
            call = connection.execute(
                "INSERT INTO oauth_requests(kind,family,presented_hash,consumed,released) VALUES (?,?,?,1,?)",
                (kind, family, hashlib.sha256(presented.encode()).hexdigest(), int(faults.get("hold") != kind)),
            ).lastrowid
        async with asyncio.timeout(120):
            while True:
                with sqlite3.connect(database) as connection:
                    released = connection.execute("SELECT released FROM oauth_requests WHERE id=?", (call,)).fetchone()[
                        0
                    ]
                if released:
                    break
                await asyncio.sleep(0.025)
        if faults.get("lost_response") == kind:
            return Response(status_code=502)
        result = {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "Bearer",
            "expires_in": expires,
            "scope": "tools",
        }
        if faults.get("no_refresh_token") or kind == "client_credentials":
            result.pop("refresh_token")
        if faults.get("no_expiry"):
            result.pop("expires_in")
        if faults.get("oversized") == kind:
            result["padding"] = "x" * 300000
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE oauth_requests SET returned=1 WHERE id=?", (call,))
        return JSONResponse(result)

    @app.get("/fixture/oauth-state")
    async def oauth_state():
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            return {
                "requests": [dict(row) for row in connection.execute("SELECT * FROM oauth_requests ORDER BY id")],
                "access": [dict(row) for row in connection.execute("SELECT * FROM oauth_access ORDER BY id")],
                "families": [
                    dict(row)
                    for row in connection.execute("SELECT id,principal,generation,revoked FROM oauth_families")
                ],
            }

    @app.post("/fixture/oauth-faults")
    async def configure_faults(request: Request):
        values = await request.json()
        with sqlite3.connect(database) as connection:
            connection.execute("DELETE FROM oauth_faults")
            connection.executemany(
                "INSERT INTO oauth_faults VALUES (?,?)", [(key, str(value)) for key, value in values.items()]
            )
        return {"configured": True}

    @app.post("/fixture/oauth-release")
    async def release():
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE oauth_requests SET released=1")
        return {"released": True}

    @app.middleware("http")
    async def protect_tools(request: Request, call_next):
        if request.url.path == "/tools/none/mcp" and request.method != "DELETE":
            bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
            with sqlite3.connect(database) as connection:
                valid = connection.execute(
                    "SELECT principal FROM oauth_families WHERE access=? AND expires>? AND revoked=0",
                    (bearer, time.time()),
                ).fetchone()
                if valid:
                    connection.execute(
                        "INSERT INTO oauth_access(principal,token_hash,method) VALUES (?,?,?)",
                        (valid[0], hashlib.sha256(bearer.encode()).hexdigest(), request.method),
                    )
            if not valid:
                origin = str(request.base_url).rstrip("/")
                return Response(
                    status_code=401,
                    headers={
                        "WWW-Authenticate": f'Bearer resource_metadata="{origin}/.well-known/oauth-protected-resource/tools/none/mcp", scope="tools"'
                    },
                )
        return await call_next(request)

    app.mount("/tools", create_mcp(database))
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    add_listener_arguments(parser)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    serve_fixture(create_app(args.database), fd=args.fd, shared_socket=args.shared_socket)
