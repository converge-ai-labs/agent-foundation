"""Counter MCP server shared by in-process HTTP and subprocess stdio tests."""

import asyncio
import os

from fastmcp import Context, FastMCP
from fastmcp.tools import ToolResult
from mcp.types import ElicitRequest, ElicitRequestFormParams, InputRequiredResult, TextContent


def create_server() -> FastMCP:
    server = FastMCP("Apps fixture")
    count = 0

    @server.tool(meta={"ui": {"resourceUri": "ui://counter/app.html"}})
    async def counter(ctx: Context, delay: float = 0, confirm: bool = False) -> ToolResult | InputRequiredResult:
        nonlocal count
        if confirm and ctx.input_responses is None:
            return InputRequiredResult(
                request_state="counter-confirm",
                input_requests={
                    "confirm": ElicitRequest(
                        params=ElicitRequestFormParams(
                            message="Confirm count",
                            requested_schema={
                                "type": "object",
                                "properties": {"name": {"type": "string"}},
                                "required": ["name"],
                            },
                        )
                    )
                },
            )
        await asyncio.sleep(delay)
        count += 1
        return ToolResult(
            content=[TextContent(type="text", text=f"count={count}")],
            structured_content={
                "count": count,
                "pid": os.getpid(),
                "capabilities": ctx.session.client_capabilities.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                ),
            },
            meta={"private": "not-for-the-model"},
        )

    @server.tool(meta={"ui": {"visibility": ["app"]}})
    def reset() -> str:
        nonlocal count
        count = 0
        return "reset"

    @server.resource("ui://counter/app.html", mime_type="text/html;profile=mcp-app")
    def app() -> str:
        return "<!doctype html><p>Counter</p>"

    @server.resource("data://counter/{name}")
    def item(name: str) -> str:
        return f"item={name};count={count}"

    return server


if __name__ == "__main__":
    create_server().run(transport="stdio", show_banner=False)
