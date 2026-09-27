from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.mcp_apps.connections import CapturedCall
from a13n_harness_ui.mcp_apps.resources import AppResourceRequest, matches_template, result_resource_uris
from mcp.types import ResourceLink

from . import test_mcp_apps_operations as fixtures

apps = fixtures.apps


def test_templates_require_exact_supported_round_trip() -> None:
    assert matches_template("data://items/a", "data://items/{id}")
    assert matches_template("data://items/a/b", "data://items/{path*}")
    assert matches_template("data://items/a?q=hello%20world", "data://items/{id}{?q}")
    assert not matches_template("data://items/a?secret=yes", "data://items/{id}")
    assert not matches_template("data://items/a?q=ok&extra=yes", "data://items/{id}{?q}")
    assert not matches_template("data://items/a?q=one&q=two", "data://items/{id}{?q}")
    assert not matches_template("https://elsewhere.invalid/a", "data://items/{id}")
    assert not matches_template("data://items/a/b", "data://items/{+path}")


def test_only_explicit_protocol_resources_confer_references() -> None:
    assert result_resource_uris(
        {
            "content": [
                {"type": "resource_link", "uri": "data://one"},
                {"type": "resource", "resource": {"uri": "data://two", "text": "body"}},
                {"type": "text", "text": "data://not-a-reference", "uri": "data://no"},
            ],
            "structuredContent": {"uri": "file:///private"},
        }
    ) == {"data://one", "data://two"}


@pytest.mark.anyio
async def test_resource_reads_are_same_server_current_and_view_bound(apps, monkeypatch) -> None:
    operations, reference = apps
    view = await operations.activate(reference)
    listed = await operations.read_resource("thread-1", view.view_id, AppResourceRequest(uri="ui://counter/app.html"))
    assert listed["contents"][0]["text"] == "<!doctype html><p>Counter</p>"
    request = AppResourceRequest(uri="data://counter/current")
    assert (await operations.read_resource("thread-1", view.view_id, request))["contents"][0][
        "text"
    ] == "item=current;count=1"
    connection = operations.snapshots.connections.get("thread-1", "mcp-docs")
    remote_read = AsyncMock(wraps=connection.client.session.read_resource)
    monkeypatch.setattr(connection.client.session, "read_resource", remote_read)
    for uri in ("file:///etc/passwd", "https://example.invalid/data", "data://counter/current?extra=yes"):
        with pytest.raises(HarnessUiError, match="not exposed"):
            await operations.read_resource("thread-1", view.view_id, AppResourceRequest(uri=uri))
    remote_read.assert_not_called()
    # A pending lane does not freeze yesterday's authority.
    async with connection.dispatch:
        waiting = asyncio.create_task(operations.read_resource("thread-1", view.view_id, request))
        await fixtures._policy(operations, "deny")
    with pytest.raises(HarnessUiError, match="denies"):
        await waiting
    await fixtures._policy(operations, "allow")
    operations.close_view("thread-1", view.view_id)
    with pytest.raises(HarnessUiError, match="closed"):
        await operations.read_resource("thread-1", view.view_id, request)


@pytest.mark.anyio
async def test_original_explicit_reference_works_without_listing_and_is_not_shared(apps, monkeypatch) -> None:
    operations, reference = apps
    connection = operations.snapshots.connections.get("thread-1", "mcp-docs")
    tool = next(tool for tool in await connection.client.list_tools() if tool.name == "counter")
    result = await connection.client.call_tool_mcp("counter", {})
    result = result.model_copy(
        update={"content": [ResourceLink(type="resource_link", name="Selected", uri="data://counter/selected")]}
    )
    linked = await operations.snapshots.capture(
        CapturedCall(connection, tool, {}, result), thread_id="thread-1", run_id="run-link", call_id="call-link"
    )
    first = await operations.activate(linked)
    second = await operations.activate(reference)
    monkeypatch.setattr(connection.client, "list_resources", AsyncMock(return_value=[]))
    monkeypatch.setattr(connection.client, "list_resource_templates", AsyncMock(return_value=[]))
    request = AppResourceRequest(uri="data://counter/selected")
    assert (await operations.read_resource("thread-1", first.view_id, request))["contents"][0][
        "text"
    ] == "item=selected;count=2"
    with pytest.raises(HarnessUiError, match="not exposed"):
        await operations.read_resource("thread-1", second.view_id, request)
