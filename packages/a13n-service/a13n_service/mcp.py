"""API-serving process MCP assembly; authentication remains owned by the Distribution."""

from collections.abc import Sequence
from importlib.metadata import version
from urllib.parse import urlsplit

import mcp.types as mt
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.http import StarletteWithLifespan
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import Tool, ToolResult
from mcp.types import ToolAnnotations
from starlette.types import ASGIApp, Receive, Scope, Send

from a13n_service.api_access import credential_context
from a13n_service.api_tools import api_tools
from a13n_service.documentation import Documents
from a13n_service.infra.platform import PLATFORM_METADATA, PlatformOperation
from a13n_service.settings import Settings
from a13n_service.tenancy.access import unauthenticated
from a13n_service.tenancy.requests import cached_credential, current_credential, current_runtime, current_workspace


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
            with credential_context(credential, scope):
                await self.app(scope, receive, send)
            return
        await self.app(scope, receive, send)


class RunToolScope(Middleware):
    """Discovery and invocation enforce the same route metadata as direct HTTP."""

    def __init__(self, tools: Sequence[Tool]):
        self.operations = {
            tool.name: PlatformOperation.model_validate(tool.meta[PLATFORM_METADATA])
            for tool in tools
            if tool.meta and PLATFORM_METADATA in tool.meta
        }

    def admitted(self, name: str) -> bool:
        credential = cached_credential(get_http_request())
        if credential is None:
            raise unauthenticated()
        platform = credential.platform
        operation = self.operations.get(name)
        return platform is None or (operation is not None and operation.group in platform.groups)

    async def on_list_tools(
        self, context: MiddlewareContext[mt.ListToolsRequest], call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]]
    ) -> Sequence[Tool]:
        return [tool for tool in await call_next(context) if self.admitted(tool.name)]

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        if not self.admitted(context.message.name):
            raise ToolError("Run credential does not admit this operation")
        return await call_next(context)


def build_mcp(app: FastAPI, settings: Settings, routes: Sequence[APIRoute]) -> StarletteWithLifespan:
    tools = api_tools(app, app.openapi(), settings.server.public_origin, routes)
    server = FastMCP(
        "a13n Service",
        version=version("a13n-service"),
        instructions="Manage workspace resources and query sessions, threads, runs and attempts. Execution control and file transfer use "
        "the HTTP API, not MCP. API tools return {status, headers, body}; retain ETags for writes. "
        "Never retry a write automatically after an unknown outcome.",
        tools=tools,
        middleware=[RunToolScope(tools)],
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
