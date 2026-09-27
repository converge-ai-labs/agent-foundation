"""State belongs to this stdio process, not to the browser or a conversational Run."""

from __future__ import annotations

import json
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from mcp.types import TextContent

APP_URI = "ui://counter/app.html"
MIME_TYPE = "text/html;profile=mcp-app"


def create_server() -> FastMCP:
    server = FastMCP("Counter App")
    count = 0

    def result() -> ToolResult:
        return ToolResult(
            content=[TextContent(type="text", text=f"The counter is {count}.")],
            structured_content={"count": count},
        )

    @server.tool(meta={"ui": {"resourceUri": APP_URI}})
    def show_counter() -> ToolResult:
        """Open the interactive counter without changing its value."""
        return result()

    @server.tool(meta={"ui": {"visibility": ["app"]}})
    def increment_counter() -> ToolResult:
        """Increment this session's counter by one."""
        nonlocal count
        count += 1
        return result()

    @server.tool(meta={"ui": {"visibility": ["app"]}})
    def reset_counter() -> ToolResult:
        """Reset the counter to zero. The example Host asks for approval."""
        nonlocal count
        count = 0
        return result()

    @server.resource(APP_URI, mime_type=MIME_TYPE)
    def app_html() -> str:
        path = Path(__file__).with_name("assets") / "app.html"
        if not path.is_file():
            raise RuntimeError("Build the App first: npm ci && npm run build in examples/mcp-apps")
        return path.read_text(encoding="utf-8")

    @server.resource("data://counter/current", mime_type="application/json")
    def current_count() -> str:
        return json.dumps({"count": count})

    return server


def main() -> None:
    create_server().run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
