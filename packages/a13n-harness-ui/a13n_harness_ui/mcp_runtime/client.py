"""SDK adapter for one selected Host-owned MCP connection."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from fastmcp import Client
from fastmcp.client.progress import ProgressHandler
from fastmcp.client.transports import ClientTransport
from mcp.types import CallToolResult, GetPromptResult, ReadResourceResult

if TYPE_CHECKING:
    from .connections import Connection, Operation


class HostClient(Client[ClientTransport]):
    """One dispatch lane and stable Host callbacks for all consumers of a client."""

    connection: Connection

    async def call_tool_mcp(
        self,
        name: str,
        arguments: dict[str, Any],
        progress_handler: ProgressHandler | None = None,
        timeout: timedelta | float | int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> CallToolResult:
        async with self.connection.operation():
            return await super().call_tool_mcp(name, arguments, progress_handler, timeout, meta)

    async def read_resource_mcp(self, uri: Any, meta: dict[str, Any] | None = None) -> ReadResourceResult:
        async with self.connection.operation():
            return await super().read_resource_mcp(uri, meta)

    async def get_prompt_mcp(
        self, name: str, arguments: dict[str, Any] | None = None, meta: dict[str, Any] | None = None
    ) -> GetPromptResult:
        async with self.connection.operation():
            return await super().get_prompt_mcp(name, arguments, meta)

    async def call_app_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        authorize: Callable[[], Awaitable[None]],
        operation: Operation | None = None,
    ) -> CallToolResult:
        async with self.app_dispatch(authorize, operation):
            return await super().call_tool_mcp(name, arguments)

    async def read_app_resource(
        self, uri: str, *, authorize: Callable[[], Awaitable[None]], operation: Operation | None = None
    ) -> ReadResourceResult:
        async with self.app_dispatch(authorize, operation):
            return await super().read_resource_mcp(uri)

    @asynccontextmanager
    async def app_dispatch(
        self, authorize: Callable[[], Awaitable[None]], operation: Operation | None = None
    ) -> AsyncIterator[None]:
        async with self.connection.operation(authorize=authorize, operation=operation):
            self.connection.require_admission()
            self.connection._borrows += 1
            try:
                yield
            finally:
                self.connection.release()
