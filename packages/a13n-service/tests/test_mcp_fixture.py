"""The counted development peer implements modern discovery and legacy negotiation."""

import json
from pathlib import Path

import httpx2
import pytest
from fastmcp import Client

from .test_connections import serve

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("protocol", ["2026-07-28", "legacy", "auto"])
async def test_counted_fixture_modern_and_legacy_without_business_replay(tmp_path, monkeypatch, protocol):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]))
    from dev.fixtures.mcp import create_app

    app = create_app(tmp_path / "fixture.sqlite")
    methods = []

    @app.middleware("http")
    async def observe(request, call_next):
        if request.method == "POST" and request.url.path.endswith("/mcp"):
            methods.append((await request.json()).get("method"))
        return await call_next(request)

    async with serve(app) as url, Client(url + "/none/mcp", mode=protocol) as client:
        assert {tool.name for tool in await client.list_tools()} == {
            "increment",
            "increment_once",
            "read_count",
            "oversized",
        }
        for expected in (1, 2):
            result = await client.call_tool_mcp("increment", {})
            assert not result.is_error
            assert json.loads(result.content[0].text)["count"] == expected
        async with httpx2.AsyncClient() as http:
            state = (await http.get(url + "/fixture/state")).json()
            assert state["effects"] == 2 and len(state["calls"]) == 2
    assert methods.count("tools/call") == 2
    if protocol == "legacy":
        assert "initialize" in methods
    else:
        assert "initialize" not in methods
        if protocol == "auto":
            assert "server/discover" in methods
        # An explicitly pinned modern version needs no discovery preflight.
