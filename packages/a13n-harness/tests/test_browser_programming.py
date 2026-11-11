"""Page programming uses the same binding, lifecycle and reference owners."""

import asyncio

import pytest
from a13n_harness.capabilities.browser import BrowserError, BrowserHumanControl
from a13n_harness.capabilities.browser.programming import CDP_EXCLUDED
from a13n_harness.capabilities.browser.scope import CdpBrowserBackend

from .test_browser_scope import scoped_browser as scoped_browser

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("value", [None, True, 42, 1.5, "hello", [1, "a"], {"items": [1, 2]}])
async def test_js_returns_json_values_and_awaits_promises(scoped_browser, value):
    browser, _ = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["one"])
    browser.client.send_raw.return_value = {"result": {"value": value}}
    browser.client.send_raw.side_effect = None
    assert await backend.js("Promise.resolve(42)") == value
    browser.client.send_raw.assert_awaited_once_with(
        "Runtime.evaluate",
        {"expression": "Promise.resolve(42)", "returnByValue": True, "awaitPromise": True, "timeout": 10000},
        "session-one",
    )


async def test_undefined_returns_null(scoped_browser):
    browser, _ = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["one"])
    browser.client.send_raw.side_effect = None
    browser.client.send_raw.return_value = {"result": {"type": "undefined"}}
    assert await backend.js("undefined") is None


@pytest.mark.parametrize("remote", [{"unserializableValue": "NaN"}, {"type": "function", "objectId": "handle"}])
async def test_js_rejects_non_json_results_explicitly(scoped_browser, remote):
    browser, _ = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["one"])
    browser.client.send_raw.side_effect = None
    browser.client.send_raw.return_value = {"result": remote}
    with pytest.raises(BrowserError, match="JSON value") as error:
        await backend.js("NaN")
    assert error.value.dispatch == "dispatched"


async def test_js_exception_reports_dispatch_and_invalidates_refs(scoped_browser):
    browser, _ = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["one"])
    backend.page.revision = "old"
    browser.client.send_raw.side_effect = None
    browser.client.send_raw.return_value = {"exceptionDetails": {"text": "Uncaught"}}
    with pytest.raises(BrowserError, match="earlier effects") as error:
        await backend.js("document.title='changed'; throw Error('later')")
    assert error.value.dispatch == "dispatched"
    assert not backend.page.revision


async def test_cdp_keeps_selected_session_and_returns_protocol_result(scoped_browser):
    browser, _ = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["one"])
    browser.client.send_raw.side_effect = None
    browser.client.send_raw.return_value = {"nodes": [{"role": {"value": "button"}}]}
    params = {"depth": 2, "pierce": True}
    assert await backend.cdp("DOM.getDocument", params) == {"nodes": [{"role": {"value": "button"}}]}
    browser.client.send_raw.assert_awaited_once_with("DOM.getDocument", params, "session-one")
    assert not backend.page.requires_observation  # Consecutive programming calls need no forced observe.
    await backend.cdp("Input.insertText", {"text": "next"})


@pytest.mark.parametrize("method", [*sorted(CDP_EXCLUDED), "Browser.close", "Target.attachToTarget", "IO.read", "bad"])
async def test_cdp_cannot_replace_host_management_file_or_event_owners(scoped_browser, method):
    browser, _ = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["one"])
    with pytest.raises(BrowserError, match="outside the selected-page") as error:
        await backend.cdp(method, {"targetId": "external", "sessionId": "session-external"})
    assert error.value.dispatch == "not_dispatched"
    browser.client.send_raw.assert_not_awaited()


@pytest.mark.parametrize("tool", ["js", "cdp"])
async def test_programming_obeys_pause_fresh_observation_and_live_revocation(scoped_browser, tool):
    browser, _ = scoped_browser
    allowed = True

    def check():
        if not allowed:
            raise BrowserError("Access revoked")

    backend = CdpBrowserBackend(browser, browser.pages["one"], check_access=check)

    async def call():
        return await backend.js("1") if tool == "js" else await backend.cdp("DOM.getDocument")

    await backend.pause()
    with pytest.raises(BrowserHumanControl):
        await call()
    backend.resume()
    with pytest.raises(BrowserError, match="Observe after handback"):
        await call()
    backend.page.requires_observation = False
    async with backend.page.lock:
        queued = asyncio.create_task(call())
        await asyncio.sleep(0)
        allowed = False
    with pytest.raises(BrowserError, match="revoked"):
        await queued
    browser.client.send_raw.assert_not_awaited()


@pytest.mark.parametrize("tool", ["js", "cdp"])
async def test_programming_drains_for_takeover_and_invalidates_on_cancellation(scoped_browser, tool):
    browser, _ = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["one"])
    entered = asyncio.Event()

    async def send(*args):
        entered.set()
        await asyncio.Event().wait()

    browser.client.send_raw.side_effect = send
    backend.page.revision = "old"
    call = asyncio.create_task(backend.js("1") if tool == "js" else backend.cdp("DOM.getDocument"))
    await entered.wait()
    pause = asyncio.create_task(backend.pause())
    await asyncio.sleep(0)
    assert not pause.done()
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    await pause
    assert not backend.page.revision
    assert backend.page.requires_observation
    assert browser.client.send_raw.await_count == 1


@pytest.mark.parametrize("tool", ["js", "cdp"])
@pytest.mark.parametrize(
    "failure", [TimeoutError(), ConnectionError(), RuntimeError("local failure"), RuntimeError({})]
)
async def test_programming_transport_failure_is_unknown_and_never_replayed(scoped_browser, tool, failure):
    browser, _ = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["one"])
    backend.page.revision = "old"
    browser.client.send_raw.side_effect = failure
    with pytest.raises(BrowserError) as error:
        if tool == "js":
            await backend.js("document.title='changed'")
        else:
            await backend.cdp("Input.insertText", {"text": "once"})
    assert error.value.dispatch == "unknown"
    assert browser.client.send_raw.await_count == 1
    assert not backend.page.revision


async def test_programming_uses_json_result_boundary(scoped_browser):
    browser, _ = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["one"])
    browser.client.send_raw.side_effect = None
    browser.client.send_raw.return_value = {"value": float("nan")}
    with pytest.raises(BrowserError, match="not a JSON"):
        await backend.cdp("Performance.getMetrics")


@pytest.mark.parametrize("code", [-32601, -32602])
async def test_cdp_protocol_rejections_are_known_and_allow_followup(scoped_browser, code):
    browser, _ = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["one"])
    browser.client.send_raw.side_effect = RuntimeError({"code": code, "message": "Invalid parameters"})
    with pytest.raises(BrowserError, match="Correct the request") as error:
        await backend.cdp("DOM.getDocument", {"depth": "bad"})
    assert error.value.dispatch == "not_dispatched"
    browser.client.send_raw.side_effect = None
    browser.client.send_raw.return_value = {"root": {"nodeName": "#document"}}
    assert (await backend.cdp("DOM.getDocument"))["root"]["nodeName"] == "#document"


@pytest.mark.parametrize("tool", ["js", "cdp"])
@pytest.mark.parametrize("code", [-32000, -32001, -32603])
async def test_programming_error_replies_settle_without_claiming_no_effects(scoped_browser, tool, code):
    browser, _ = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["one"])
    backend.page.revision = "old"
    browser.client.send_raw.side_effect = RuntimeError({"code": code, "message": "Command failed"})
    with pytest.raises(BrowserError, match="earlier effects") as error:
        if tool == "js":
            await backend.js("document.title='changed'")
        else:
            await backend.cdp("DOM.getOuterHTML", {"nodeId": 999999})
    assert error.value.dispatch == "dispatched"
    assert browser.client.send_raw.await_count == 1
    assert not backend.page.revision
    browser.client.send_raw.side_effect = None
    browser.client.send_raw.return_value = {"result": {"value": 4}}
    assert await backend.js("2+2") == 4


@pytest.mark.parametrize("tool", ["js", "cdp"])
async def test_programming_dialog_yields_to_recovery_without_replaying(scoped_browser, tool):
    browser, _ = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["one"])
    resumed = asyncio.Event()

    async def send(*args):
        backend.page.dialog = {"type": "alert", "message": "test"}
        backend.page.dialog_opened.set()
        await resumed.wait()
        return {"result": {"value": 42}}

    browser.client.send_raw.side_effect = send
    with pytest.raises(BrowserError, match="Use browser_dialog") as error:
        if tool == "js":
            await backend.js("alert('test'); 42")
        else:
            await backend.cdp("Runtime.evaluate", {"expression": "alert('test'); 42"})
    assert error.value.dispatch == "dispatched"
    assert browser.tasks
    resumed.set()
    await asyncio.gather(*browser.tasks)
    assert browser.client.send_raw.await_count == 1


async def test_cdp_pending_dialog_rejects_document_commands_but_allows_handling(scoped_browser):
    browser, _ = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["one"])
    backend.page.dialog = {"type": "alert", "message": "test"}
    backend.page.dialog_opened.set()
    with pytest.raises(BrowserError, match="dialog is pending") as error:
        await backend.cdp("Runtime.evaluate", {"expression": "42"})
    assert error.value.dispatch == "not_dispatched"
    browser.client.send_raw.assert_not_awaited()
    assert await backend.cdp("Page.handleJavaScriptDialog", {"accept": True}) == {}
    browser.client.send_raw.assert_awaited_once_with("Page.handleJavaScriptDialog", {"accept": True}, "session-one")


@pytest.mark.parametrize("files", [["/host/private.txt"], ["relative.txt"]])
async def test_cdp_drag_cannot_supply_host_files(scoped_browser, files):
    browser, _ = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["one"])
    with pytest.raises(BrowserError, match="browser_files") as error:
        await backend.cdp("Input.dispatchDragEvent", {"data": {"files": files}})
    assert error.value.dispatch == "not_dispatched"
    browser.client.send_raw.assert_not_awaited()


@pytest.mark.parametrize("files", [None, []])
async def test_cdp_file_free_drag_remains_available(scoped_browser, files):
    browser, _ = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["one"])
    data = {"items": [{"mimeType": "text/plain", "data": "drag text"}], "dragOperationsMask": 1}
    if files is not None:
        data["files"] = files
    params = {"type": "dragEnter", "x": 20, "y": 20, "data": data}
    assert await backend.cdp("Input.dispatchDragEvent", params) == {}
    browser.client.send_raw.assert_awaited_once_with("Input.dispatchDragEvent", params, "session-one")
