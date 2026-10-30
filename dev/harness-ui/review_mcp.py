"""Fictional MCP inventory for local UI review; never makes external requests."""

from fastmcp import FastMCP

server = FastMCP("TinyFish review fixture")


@server.tool
async def search(query: str, limit: int = 10) -> dict:
    """Search the web for relevant pages, articles, and documentation."""
    return {"results": [], "fixture": True, "query": query, "limit": limit}


@server.tool
async def fetch_content(url: str, format: str = "markdown") -> dict:
    """Fetch a page and extract its readable content as Markdown or plain text."""
    return {"content": "Local review fixture. No page was fetched.", "url": url, "format": format}


INVENTORY = {
    "browser": ["open", "click", "type", "scroll", "screenshot", "close", "back", "wait", "tabs", "select", "hover"],
    "monitor": ["create", "list", "get", "update", "pause", "resume", "delete"],
    "dataset": ["create", "list", "get", "query", "export", "update", "delete"],
    "wallet": ["balance", "transactions", "transfer", "addresses", "estimate"],
    "session": ["create", "list", "get", "close", "history"],
    "workflow": ["create", "run", "list", "delete"],
}

for category, actions in INVENTORY.items():
    for action in actions:

        async def fixture(identifier: str = "demo") -> dict:
            return {"fixture": True, "identifier": identifier}

        server.tool(
            fixture,
            name=f"{category}_{action}",
            description=f"{action.capitalize()} {category} data. Local preview fixture; no external actions are performed.",
        )

if __name__ == "__main__":
    server.run()
