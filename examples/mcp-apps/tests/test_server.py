from __future__ import annotations

import json
import sys

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from mcp.types import TextResourceContents

from mcp_apps_example.server import APP_URI, MIME_TYPE, create_server


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_tools_resource_and_session_state_use_real_stdio() -> None:
    transport = StdioTransport(command=sys.executable, args=["-m", "mcp_apps_example.server"])
    async with Client(transport) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert (tools["show_counter"].meta or {})["ui"] == {"resourceUri": APP_URI}
        assert (tools["increment_counter"].meta or {})["ui"] == {"visibility": ["app"]}
        assert (await client.call_tool("show_counter", {})).structured_content == {"count": 0}
        assert (await client.call_tool("increment_counter", {})).structured_content == {"count": 1}
        assert (await client.call_tool("increment_counter", {})).structured_content == {"count": 2}
        resource = (await client.read_resource("data://counter/current"))[0]
        assert isinstance(resource, TextResourceContents)
        assert json.loads(resource.text) == {"count": 2}
        assert (await client.call_tool("reset_counter", {})).structured_content == {"count": 0}
        html = (await client.read_resource(APP_URI))[0]
        assert html.mime_type == MIME_TYPE
        assert isinstance(html, TextResourceContents)
        assert "Session counter" in html.text
        assert "<!-- APP_SCRIPT -->" not in html.text
        assert "<script>" in html.text
        assert "<script src=" not in html.text


@pytest.mark.anyio
async def test_new_server_has_new_business_state() -> None:
    async with Client(create_server()) as first:
        await first.call_tool("increment_counter", {})
    async with Client(create_server()) as second:
        assert (await second.call_tool("show_counter", {})).structured_content == {"count": 0}
