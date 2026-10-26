from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import httpx2
import pytest
from a13n_harness.capabilities import BrowserUseCapability
from a13n_harness.capabilities.browser import BrowserError
from a13n_harness.capabilities.browser.actions import Click, Press, Ref
from a13n_harness.capabilities.browser.cdp import Chromium, Page, websocket_url
from a13n_harness.capabilities.browser.interaction import interact
from a13n_harness.capabilities.browser.scope import CdpBrowserBackend

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
        if method == "DOMSnapshot.captureSnapshot":
            return {"strings": [], "documents": []}
        if method == "Accessibility.getFullAXTree":
            return {"nodes": [{"role": {"value": "button"}, "name": {"value": "Continue"}, "backendDOMNodeId": 11}]}
        if method == "DOM.resolveNode":
            return {"object": {"objectId": "node-one"}}
        if method == "Runtime.callFunctionOn":
            if params["functionDeclaration"].startswith("function(x,y)"):
                return {"result": {"value": True}}
            return {"result": {"value": {"x": 20, "y": 30, "type": "button"}}}
        return {}

    browser.client.send_raw.side_effect = send
    tools = BrowserUseCapability(CdpBrowserBackend(browser, page)).get_toolset().tools
    observation = await tools["browser_observe"].function_schema.call({}, None)
    assert '[e1] button: "Continue"' in observation
    schema = tools["browser_act"].function_schema
    args = schema.validator.validate_python({"action": {"kind": "click", "target": {"kind": "ref", "ref": "e1"}}})
    assert '"dispatch": "dispatched"' in await schema.call(args, None)
    mutations = [
        call.args for call in browser.client.send_raw.await_args_list if call.args[0] == "Input.dispatchMouseEvent"
    ]
    assert [args[1]["type"] for args in mutations] == ["mouseMoved", "mousePressed", "mouseReleased"]
    browser.client.send_raw.assert_any_await("Runtime.releaseObject", {"objectId": "node-one"}, page.session)


async def test_documents_follow_exact_nested_target_ancestry_without_attach_events(browser):
    page = Page("owned", "session-root")
    # Deliberately reverse the nesting order and share URLs with unrelated targets.
    infos = [
        {"targetId": target, "type": "iframe", "parentFrameId": parent, "url": "https://same.example/"}
        for target, parent in (("deep", "child"), ("foreign", "other-root"), ("child", "root"))
    ]
    infos.append({"targetId": "unlinked", "type": "iframe", "url": "https://same.example/"})

    async def send(method, params, session):
        if method == "Target.getTargets":
            return {"targetInfos": infos}
        if method == "Target.attachToTarget":
            assert params["targetId"] in {"child", "deep"}
            return {"sessionId": "session-" + params["targetId"]}
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": session.removeprefix("session-"), "url": "https://same.example/"}}}
        return {}

    browser.client.send_raw.side_effect = send
    assert not page.frame_parents
    frames = await browser.documents(page)
    assert [(frame.identity, frame.parent, frame.session) for frame in frames] == [
        ("root", "", "session-root"),
        ("child", "root", "session-child"),
        ("deep", "child", "session-deep"),
    ]
    assert set(page.frame_sessions) == {"child", "deep"}


async def test_direct_connection_closes_only_its_page_on_exception(monkeypatch, browser):
    page = Page("owned", "session")
    browser.pages = {page.target: page, "existing": Page("existing", "other-session")}
    browser.create = AsyncMock(return_value=page)
    targets = set(browser.pages)

    async def send(method, params, session):
        if method == "Target.closeTarget":
            targets.discard(params["targetId"])
        return {"targetInfos": [{"targetId": key, "type": "page"} for key in targets]}

    browser.client.send_raw.side_effect = send
    connect = AsyncMock(return_value=browser)
    monkeypatch.setattr(Chromium, "connect", connect)
    with pytest.raises(ValueError, match="run failed"):
        async with CdpBrowserBackend.connect("ws://localhost:9222/devtools/browser/test") as backend:
            assert backend.page is page and backend.browser is browser
            raise ValueError("run failed")
    browser.client.send_raw.assert_any_await("Target.closeTarget", {"targetId": "owned"}, None)
    assert set(browser.pages) == set()  # Disconnect drops sessions, not remote targets.
    assert [call.args[0] for call in browser.client.send_raw.await_args_list].count("Target.closeTarget") == 1
    assert "Browser.close" not in [call.args[0] for call in browser.client.send_raw.await_args_list]
    browser.client.stop.assert_awaited_once()


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
    assert "Observe after handback" in await interact(browser, page, Press(kind="press", key="Enter"))
    page.requires_observation = False
    assert "Unsupported" in await interact(browser, page, Press(kind="press", key="Unknown"))
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
        if method == "DOMSnapshot.captureSnapshot":
            return {"strings": [], "documents": []}
        if method == "Accessibility.getFullAXTree":
            return {"nodes": [{"role": {"value": "button"}, "name": {"value": "Alert"}, "backendDOMNodeId": 11}]}
        if method == "DOM.resolveNode":
            return {"object": {"objectId": "alert-node"}}
        if method == "Runtime.callFunctionOn":
            if params["functionDeclaration"].startswith("function(x,y)"):
                return {"result": {"value": True}}
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
        interact(
            browser,
            page,
            Click(kind="click", target=Ref(ref=observation.elements[0].target)),
            revision=observation.revision,
        ),
        1,
    )
    assert '"dialog_pending": true' in result
    assert page.deferred_objects
    with pytest.raises(BrowserError, match="dialog"):
        await CdpBrowserBackend(browser, page).screenshot()
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


@pytest.mark.parametrize("stage", ["resolve", "geometry", "hover", "focus", "upload"])
async def test_navigation_before_mutation_rejects_stale_target(browser, stage):
    from a13n_harness.capabilities.browser.cdp import Frame, Node

    page = Page("one", "session", revision="observed")
    page.frames["frame"] = Frame("frame", "session")
    page.elements["ref"] = Node(1, "frame", "session")
    browser.pages[page.target] = page

    async def send(method, params, session):
        invalidate = (
            (stage == "resolve" and method == "DOM.resolveNode")
            or (stage == "geometry" and method == "Runtime.callFunctionOn")
            or (stage == "hover" and method == "Input.dispatchMouseEvent" and params["type"] == "mouseMoved")
            or (
                stage == "focus"
                and method == "Runtime.callFunctionOn"
                and "this.focus(" in params["functionDeclaration"]
            )
            or (stage == "upload" and method == "Runtime.callFunctionOn")
        )
        if invalidate:
            browser._within_document({"frameId": "frame", "url": "https://example.com/changed"}, session)
        if method == "DOM.resolveNode":
            return {"object": {"objectId": "node"}}
        if method == "Runtime.callFunctionOn":
            return {"result": {"value": {"x": 10, "y": 10, "type": "text", "multiple": True}}}
        return {}

    browser.client.send_raw.side_effect = send
    with pytest.raises(BrowserError, match="expired"):
        if stage == "upload":
            await browser.upload(page, "ref", ("/host/file.txt",), revision="observed")
        else:
            await browser.element(page, "ref", "observed", fill="text" if stage == "focus" else None)
    for call in browser.client.send_raw.await_args_list:
        method, params, _ = call.args
        assert method not in {"Input.insertText", "DOM.setFileInputFiles"}
        assert method != "Input.dispatchMouseEvent" or params["type"] == "mouseMoved"
    browser.client.send_raw.assert_any_await("Runtime.releaseObject", {"objectId": "node"}, "session")


async def test_navigation_caused_by_mouse_press_still_releases_gesture(browser):
    from a13n_harness.capabilities.browser.cdp import Frame, Node

    page = Page("one", "session", revision="observed")
    page.frames["frame"] = Frame("frame", "session")
    page.elements["ref"] = Node(1, "frame", "session")
    browser.pages[page.target] = page

    async def send(method, params, session):
        if method == "DOM.resolveNode":
            return {"object": {"objectId": "node"}}
        if method == "Runtime.callFunctionOn":
            if params["functionDeclaration"].startswith("function(x,y)"):
                return {"result": {"value": True}}
            return {"result": {"value": {"x": 10, "y": 10, "type": "button"}}}
        if method == "Input.dispatchMouseEvent" and params["type"] == "mousePressed":
            browser._within_document({"frameId": "frame", "url": "https://example.com/result"}, session)
        return {}

    browser.client.send_raw.side_effect = send
    assert '"dispatch": "dispatched"' in await interact(
        browser, page, Click(kind="click", target=Ref(ref="ref")), revision="observed"
    )
    events = [
        call.args[1]["type"]
        for call in browser.client.send_raw.await_args_list
        if call.args[0] == "Input.dispatchMouseEvent"
    ]
    assert events == ["mouseMoved", "mousePressed", "mouseReleased"]


@pytest.mark.parametrize("failed_key", ["A", "Shift"])
async def test_key_release_failure_does_not_skip_remaining_modifiers(browser, failed_key):
    import json

    page = Page("page", "session")
    page.requires_observation = False

    async def send(method, params, session):
        if params.get("type") == "keyUp" and params["key"] == failed_key:
            raise BrowserError("release interrupted", dispatch="unknown")
        return {}

    browser.client.send_raw.side_effect = send
    result = json.loads(await interact(browser, page, Press(kind="press", key="Control+Shift+A")))
    assert result["dispatch"] == "unknown"
    assert "Key release failed" in result["error"]
    events = [call.args[1] for call in browser.client.send_raw.await_args_list]
    assert [(e["type"], e["key"]) for e in events] == [
        ("rawKeyDown", "Control"),
        ("rawKeyDown", "Shift"),
        ("keyDown", "A"),
        ("keyUp", "A"),
        ("keyUp", "Shift"),
        ("keyUp", "Control"),
    ]
    assert [e["modifiers"] for e in events[-3:]] == [10, 2, 0]
