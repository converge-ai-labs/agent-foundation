"""Fresh native Toolsets borrow a client without owning its outer lifetime."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Self

from a13n_harness.context import AgentContext
from anyio import CancelScope
from pydantic_ai import RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import ToolsetTool

from .connections import Connection, Operation, operation_context


class HostToolset(MCPToolset[AgentContext]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection.client,
            id=connection.server_id,
            cache_tools=False,
            cache_resources=False,
            cache_prompts=False,
            prefer_tasks=False,
        )
        self.connection = connection
        self._entries = 0

    async def __aenter__(self) -> Self:
        if self._entries:
            self.connection.require_connected()
        else:
            self.connection.require_admission()
        self._entries += 1
        self.connection._borrows += 1
        try:
            return await super().__aenter__()
        except BaseException:
            self._entries -= 1
            self.connection.release()
            raise

    async def __aexit__(self, *args: Any) -> bool | None:
        with CancelScope(shield=True):
            try:
                return await super().__aexit__(*args)
            finally:
                self._entries -= 1
                self.connection.release()

    async def call_app_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        authorize: Callable[[], Awaitable[None]],
        operation: Operation | None = None,
    ) -> dict[str, Any]:
        async with self.connection.client.app_dispatch(authorize, operation):
            return await self.call_tool_for_app(name, arguments)

    async def read_app_resource(
        self, uri: str, *, authorize: Callable[[], Awaitable[None]], operation: Operation | None = None
    ) -> dict[str, Any]:
        async with self.connection.client.app_dispatch(authorize, operation):
            return await self.read_resource_for_app(uri)

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]
    ) -> Any:
        token = operation_context.set(Operation(run_id=ctx.deps.run_id, tool_call_id=ctx.tool_call_id))
        try:
            return await super().call_tool(name, tool_args, ctx, tool)
        finally:
            operation_context.reset(token)
