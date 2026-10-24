from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import httpx2
import pytest
from a13n_harness.capabilities import BrowserUseCapability
from a13n_harness.capabilities.browser import BrowserError
from a13n_harness.capabilities.browser.cdp import CdpBrowserBackend, Chromium, Page, websocket_url

pytestmark = pytest.mark.anyio


@pytest.fixture
def browser():
    client = Mock()
    client.pending_requests = {}
    client.send_raw = AsyncMock(return_value={})
    client.stop = AsyncMock()
    return Chromium(client)


async def test_standalone_backend_supplies_capability_tools_without_ui(browser):
    page = Page("page-one", "session-one")
    browser.pages[page.target] = page

    async def send(method, params, session):
        if method == "Target.getTargets":
            assert session is None
            return {"targetInfos": []}
        assert session == page.session
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": "frame-one", "url": page.url}}}
        if method == "Accessibility.getFullAXTree":
            return {"nodes": [{"role": {"value": "button"}, "name": {"value": "Continue"}, "backendDOMNodeId": 11}]}
        if method == "DOM.resolveNode":
            return {"object": {"objectId": "node-one"}}
        if method == "Runtime.callFunctionOn":
            return {"result": {"value": {"x": 20, "y": 30, "type": "button"}}}
        return {}

    browser.client.send_raw.side_effect = send
    tools = BrowserUseCapability(CdpBrowserBackend(browser, page)).get_toolset().tools
    observation = await tools["browser_observe"].function_schema.call({}, None)
    assert "[e1] Continue" in observation
    assert "completed" in await tools["browser_click"].function_schema.call({"ref": "e1"}, None)
    mutations = [
        call.args for call in browser.client.send_raw.await_args_list if call.args[0] == "Input.dispatchMouseEvent"
    ]
    assert [args[1]["type"] for args in mutations] == ["mouseMoved", "mousePressed", "mouseReleased"]
    browser.client.send_raw.assert_any_await("Runtime.releaseObject", {"objectId": "node-one"}, page.session)


async def test_direct_connection_closes_only_its_page_on_exception(monkeypatch):
    browser = AsyncMock()
    page = Page("owned", "session")
    browser.pages = {page.target: page, "existing": Page("existing", "other-session")}
    browser.create.return_value = page
    connect = AsyncMock(return_value=browser)
    monkeypatch.setattr(Chromium, "connect", connect)
    with pytest.raises(ValueError, match="run failed"):
        async with CdpBrowserBackend.connect("ws://localhost:9222/devtools/browser/test") as backend:
            assert backend.page is page and backend.browser is browser
            raise ValueError("run failed")
    browser.close_page.assert_awaited_once_with(page)
    browser.close.assert_awaited_once()
    browser.close_browser.assert_not_awaited()


async def test_cdp_discovery_accepts_http_base_urls_and_preserves_query(monkeypatch):
    original = httpx2.AsyncClient
    urls = []

    def response(request):
        urls.append(str(request.url))
        return httpx2.Response(200, json={"webSocketDebuggerUrl": "wss://browser.example/devtools/browser/test"})

    monkeypatch.setattr(
        httpx2, "AsyncClient", lambda **kwargs: original(transport=httpx2.MockTransport(response), **kwargs)
    )
    assert (
        await websocket_url("https://browser.example/cdp?token=example")
        == "wss://browser.example/devtools/browser/test"
    )
    assert urls == ["https://browser.example/cdp/json/version?token=example"]
    assert (
        await websocket_url("ws://localhost:9222/devtools/browser/test") == "ws://localhost:9222/devtools/browser/test"
    )


async def test_discovery_failure_is_a_browser_error(monkeypatch):
    original = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2,
        "AsyncClient",
        lambda **kwargs: original(
            transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json={})), **kwargs
        ),
    )
    with pytest.raises(BrowserError, match="Cannot discover"):
        await websocket_url("http://localhost:9222")
    with pytest.raises(BrowserError, match="CDP base URL"):
        await websocket_url("file:///tmp/browser")


async def test_page_input_requires_handback_observation_and_a_supported_key(browser):
    page = Page("one", "session", requires_observation=True)
    with pytest.raises(BrowserError, match="Observe after handback"):
        await browser.act(page, "press", value="Enter")
    page.requires_observation = False
    with pytest.raises(BrowserError, match="Unsupported"):
        await browser.act(page, "press", value="Unknown")
    browser.client.send_raw.assert_not_awaited()


async def test_closed_socket_is_a_browser_error_and_does_not_accumulate_requests():
    from cdp_use import CDPClient
    from websockets.exceptions import ConnectionClosedError

    client = CDPClient("ws://localhost:9222/devtools/browser/fixture")
    client.ws = AsyncMock()
    client.ws.send.side_effect = ConnectionClosedError(None, None)
    browser = Chromium(client)
    try:
        for _ in range(3):
            with pytest.raises(BrowserError, match="connection closed"):
                await browser.send("Target.getTargets")
            assert not client.pending_requests
    finally:
        await browser.close()


async def test_dialog_completion_does_not_wait_for_paused_input_or_leak_node_handles(browser):
    page = Page("one", "main")
    browser.pages[page.target] = page
    released = asyncio.Event()

    async def send(method, params, session):
        if method == "Target.getTargets":
            return {"targetInfos": []}
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": "frame-one", "url": page.url}}}
        if method == "Accessibility.getFullAXTree":
            return {"nodes": [{"role": {"value": "button"}, "name": {"value": "Alert"}, "backendDOMNodeId": 11}]}
        if method == "DOM.resolveNode":
            return {"object": {"objectId": "alert-node"}}
        if method == "Runtime.callFunctionOn":
            return {"result": {"value": {"x": 5, "y": 5, "type": "button"}}}
        if method == "Input.dispatchMouseEvent" and params["type"] == "mouseReleased":
            browser._dialog_opened({"type": "alert", "message": "fixture"}, page.session)
            await released.wait()
        if method == "Page.handleJavaScriptDialog":
            browser._dialog_closed({}, page.session)
            released.set()
        return {}

    browser.client.send_raw.side_effect = send
    observation = await browser.observe(page)
    result = await asyncio.wait_for(
        browser.act(page, "click", target=observation.elements[0].target, revision=observation.revision), 1
    )
    assert "dialog pending" in result
    assert page.deferred_objects
    with pytest.raises(BrowserError, match="dialog"):
        await browser.screenshot(page)
    assert "answered" in await browser.dialog(page, "accept")
    await asyncio.gather(*browser.tasks)
    assert not page.deferred_objects
    browser.client.send_raw.assert_any_await("Runtime.releaseObject", {"objectId": "alert-node"}, page.session)


async def test_download_events_never_list_another_page_or_unknown_frame(browser):
    import json

    from a13n_harness.capabilities.browser.cdp import Frame

    one, two = Page("one", "a"), Page("two", "b")
    one.frames["frame-a"] = Frame("frame-a", "a")
    two.frames["frame-b"] = Frame("frame-b", "b")
    browser.pages = {one.target: one, two.target: two}
    await browser.configure_downloads("/browser-host/downloads")
    for frame, guid in (("frame-a", "download-a"), ("frame-b", "download-b"), ("unowned", "unknown")):
        browser._download_started({"frameId": frame, "guid": guid, "suggestedFilename": "fixture.txt"}, None)
        browser._download_progress({"guid": guid, "state": "completed"}, None)
    assert json.loads(browser.downloads({"one"})) == [
        {"name": "fixture.txt", "state": "completed", "path": "/browser-host/downloads/download-a"}
    ]
    assert "unknown" not in browser.download_records


async def test_scroll_and_upload_reject_stale_references_and_non_host_paths(browser):
    page = Page("one", "session")
    with pytest.raises(BrowserError, match="expired"):
        await browser.scroll(page, x=0, y=10, target="old", revision="old")
    with pytest.raises(BrowserError, match="absolute"):
        await browser.upload(page, "old", ("client-file.txt",), revision="old")
    browser.client.send_raw.assert_not_awaited()


async def test_failed_attach_rolls_back_its_partial_page_and_detaches_session(browser):
    async def send(method, params, session):
        if method == "Target.attachToTarget":
            return {"sessionId": "partial"}
        if method == "Runtime.enable":
            raise RuntimeError("target disappeared")
        return {}

    browser.client.send_raw.side_effect = send
    with pytest.raises(BrowserError):
        await browser.attach("gone")
    assert "gone" not in browser.pages
    browser.client.send_raw.assert_any_await("Target.detachFromTarget", {"sessionId": "partial"}, None)
