"""Credential-free, separate-origin proxy listener for opaque MCP App Views."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

from a13n_harness_ui.configuration.models import McpAppsSandboxConfiguration

from .origins import origin as origin


def content_security_policy(host: str, declarations: object) -> str:
    if not isinstance(declarations, dict):
        raise ValueError("CSP must be an object.")

    def domains(key: str) -> str:
        values = declarations.get(key, [])
        if not isinstance(values, list) or len(values) > 64:
            raise ValueError("CSP domain lists must contain at most 64 origins.")
        result = []
        for value in values:
            if not isinstance(value, str):
                raise ValueError("CSP domains must be strings.")
            # First profile intentionally accepts exact HTTP(S) origins, not wildcards.
            selected = origin(value)
            if selected == host:
                raise ValueError("App CSP cannot grant network access to the Host origin.")
            result.append(selected)
        return " ".join(result)

    resources = domains("resourceDomains")
    connections = domains("connectDomains") or "'none'"
    frames = domains("frameDomains")
    # The inner srcdoc inherits this HTTP policy and cannot loosen it with meta tags.
    return "; ".join(
        [
            "default-src 'none'",
            f"script-src 'unsafe-inline' {resources}",
            f"style-src 'unsafe-inline' {resources}",
            f"img-src data: blob: {resources}",
            f"font-src data: {resources}",
            f"media-src blob: {resources}",
            f"connect-src {connections}",
            f"frame-src 'self' {frames}",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'none'",
            f"frame-ancestors {host}",
        ]
    )


def create_sandbox() -> Starlette:
    html = Path(__file__).with_name("sandbox.html").read_text(encoding="utf-8")

    async def proxy(request: Request) -> Response:
        try:
            host = origin(request.query_params.get("host", ""))
            declarations = json.loads(request.query_params.get("csp", "{}"))
            policy = content_security_policy(host, declarations)
        except (ValueError, TypeError):
            return Response("Invalid sandbox origin or CSP.", status_code=400)
        return HTMLResponse(
            html,
            headers={
                "Content-Security-Policy": policy,
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=(), clipboard-write=()",
            },
        )

    return Starlette(routes=[Route("/sandbox.html", proxy)])


class _SandboxServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        # The outer WebUI server owns process signals.
        yield


@asynccontextmanager
async def serve_sandbox(configuration: McpAppsSandboxConfiguration) -> AsyncIterator[str]:
    """Own one static listener; no signals, auth middleware, or application API."""
    address = ipaddress.ip_address(configuration.bind)
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((configuration.bind, configuration.port))
        listener.listen()
        listener.setblocking(False)
        port = listener.getsockname()[1]
        host = f"[{address}]" if address.version == 6 else str(address)
        public = configuration.public_url or f"http://{host}:{port}"
        public = origin(public)
        server = _SandboxServer(uvicorn.Config(create_sandbox(), log_level="warning", lifespan="off", access_log=False))
        task = asyncio.create_task(server.serve(sockets=[listener]), name="mcp-app-sandbox")
        try:
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("MCP App sandbox did not start.")
                await asyncio.sleep(0.01)
            yield f"{public}/sandbox.html"
        finally:
            server.should_exit = True
            await task
