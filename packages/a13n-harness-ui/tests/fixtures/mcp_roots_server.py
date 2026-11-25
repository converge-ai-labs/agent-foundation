"""A protocol-level MCP Roots probe, runnable over stdio or HTTP."""

import asyncio

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


def roots_server() -> Server:
    changes = 0

    async def list_tools(ctx, params):
        return types.ListToolsResult(tools=[types.Tool(name="inspect", inputSchema={"type": "object"})])

    async def call_tool(ctx, params):
        capabilities = ctx.session.client_capabilities
        roots = capabilities.roots if capabilities is not None else None
        result = await ctx.session.list_roots() if roots is not None else None
        return types.CallToolResult(
            content=[],
            structuredContent={
                "advertised": roots is not None,
                "list_changed": roots.list_changed if roots is not None else None,
                "changes": changes,
                "roots": [item.model_dump(mode="json", exclude_none=True) for item in result.roots]
                if result is not None
                else [],
            },
        )

    async def roots_changed(ctx, params):
        nonlocal changes
        changes += 1

    return Server("roots", on_list_tools=list_tools, on_call_tool=call_tool, on_roots_list_changed=roots_changed)


async def _stdio() -> None:
    async with stdio_server() as (reader, writer):
        server = roots_server()
        await server.run(reader, writer, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_stdio())
