"""Real MCP peer rejection and effect-with-lost-response fixtures."""

import re

import anyio
from fastapi import HTTPException


async def fault_result(root, body):
    value = body["params"]["arguments"]["value"]
    match = re.fullmatch(r"LIVE_FAULT:(error|lost_response):([a-f0-9]{32})", value)
    if match is None:
        return None
    kind, case_id = match.groups()
    path = anyio.Path(root / case_id)
    if not await (path / "case.json").is_file():
        raise HTTPException(404, "Unknown owned case")
    if kind == "lost_response":
        # External effect exists, but no successful tools/call result reaches
        # the client. The client must report uncertainty, never replay blindly.
        await (path / "mcp-effect").write_text(value)
        raise HTTPException(503, "Injected response loss after remote effect")
    return {"content": [{"type": "text", "text": "Injected remote tool error"}], "isError": True}
