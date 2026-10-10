"""API-serving process MCP assembly; authentication remains owned by the Distribution."""

from collections.abc import Sequence
from importlib.metadata import version
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from fastmcp import FastMCP
from fastmcp.server.http import StarletteWithLifespan
from fastmcp.tools import Tool
from mcp.types import ToolAnnotations
from starlette.types import ASGIApp, Receive, Scope, Send

from a13n_service.api_tools import api_tools
from a13n_service.documentation import Documents
from a13n_service.settings import Settings
from a13n_service.tenancy.access import unauthenticated
from a13n_service.tenancy.requests import current_credential, current_runtime, current_workspace


class WorkspaceKey:
    """Run before the child changes request.app; never authenticate through browser cookies."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            # Remove cookies even for authenticators that would otherwise prefer a session over Bearer.
            scope = {**scope, "headers": [(key, value) for key, value in scope["headers"] if key != b"cookie"]}
            request = Request(scope)
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not token.strip():
                raise unauthenticated()
            credential = await current_credential(request, await current_runtime(request))
            if credential.principal.confinement is None:
                raise unauthenticated()
            await current_workspace(credential.principal, request.headers.get("x-workspace-id"))
        await self.app(scope, receive, send)


def build_mcp(app: FastAPI, settings: Settings, routes: Sequence[APIRoute]) -> StarletteWithLifespan:
    server = FastMCP(
        "a13n Service",
        version=version("a13n-service"),
        instructions="Manage workspace resources and inspect read-only traces. Execution and file transfer use "
        "the HTTP API, not MCP. API tools return {status, headers, body}; retain ETags for writes. "
        "Never retry a write automatically after an unknown outcome.",
        tools=api_tools(app, app.openapi(), settings.server.public_origin, routes),
        on_duplicate="error",
        tasks=False,
        mask_error_details=True,
        dereference_schemas=False,
    )
    server.add_tool(
        Tool.from_function(
            Documents().search,
            name="search_documents",
            annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
        )
    )
    return server.http_app(
        path="/",
        stateless_http=True,
        json_response=True,
        host_origin_protection=True,
        allowed_hosts=[urlsplit(origin).netloc for origin in settings.server.public_origins],
        allowed_origins=sorted(settings.server.public_origins),
    )
