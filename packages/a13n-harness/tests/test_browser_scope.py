from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness.capabilities.browser import BrowserError, BrowserHumanControl, BrowserObservation
from a13n_harness.capabilities.browser.cdp import Chromium, Page
from a13n_harness.capabilities.browser.scope import CdpBrowserBackend

pytestmark = pytest.mark.anyio


@pytest.fixture
def scoped_browser():
    client = Mock(pending_requests={})
    client.stop = AsyncMock()
    browser = Chromium(client)
    infos = {
        name: {"targetId": name, "type": "page", "title": name, "url": "about:blank"}
        for name in ("one", "two", "external")
    }
    browser.pages = {name: Page(name, "session-" + name) for name in infos}

    async def send(method, params, session):
        if method == "Target.getTargets":
            return {"targetInfos": list(infos.values())}
        if method == "Target.closeTarget":
            infos.pop(params["targetId"], None)
        if method == "Target.attachToTarget":
            return {"sessionId": "session-" + params["targetId"]}
        return {}

    client.send_raw = AsyncMock(side_effect=send)
    return browser, infos


async def test_membership_selection_popups_and_external_close(scoped_browser):
    browser, infos = scoped_browser
    one = CdpBrowserBackend(browser, browser.pages["one"])
    two = CdpBrowserBackend(browser, browser.pages["two"])
    with pytest.raises(BrowserError, match="another"):
        CdpBrowserBackend(browser, browser.pages["one"])
    with pytest.raises(BrowserError, match="not owned"):
        await one.tabs("select", target="two")
    with pytest.raises(BrowserError, match="another"):
        await one.attach("two")
    infos["popup"] = {"targetId": "popup", "type": "page", "openerId": "one", "url": "about:blank"}
    browser._created({"targetInfo": infos["popup"]}, None)
    assert one.targets == {"one", "popup"}
    assert [tab.target for tab in await two.tabs("list")] == ["two"]
    assert one.page.target == "one"  # A popup never steals selection.
    await one.tabs("select", target="popup")
    assert one.page.target == "popup" and one.page.requires_observation
    infos.pop("popup")
    browser._destroyed({"targetId": "popup"}, None)
    assert one.page.target == "one" and one.page.requires_observation
    await one.close()
    assert set(infos) == {"two", "external"}
    assert not one.targets
    assert two.page.target == "two"
    with pytest.raises(BrowserError, match="closed"):
        await one.tabs("list")


async def test_missing_destroy_event_is_reconciled_without_url_guessing(scoped_browser):
    browser, infos = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["one"])
    infos.pop("one")
    assert await backend.tabs("list") == ()
    assert not backend.targets
    with pytest.raises(BrowserError, match="No tabs"):
        await backend.observe()
    with pytest.raises(BrowserError, match="no longer exists"):
        await backend.attach("one")
    assert not backend.targets  # Same-URL external tabs are not replacements.


async def test_scopes_overlap_but_same_scope_operations_serialize(scoped_browser):
    browser, _ = scoped_browser
    one, two = (CdpBrowserBackend(browser, browser.pages[name]) for name in ("one", "two"))
    entered, release = asyncio.Event(), asyncio.Event()
    visited = []

    async def observe(page):
        visited.append(page.target)
        if page.target == "one":
            entered.set()
            await release.wait()
        return BrowserObservation(page.target, "fresh", ())

    browser.observe = observe
    first = asyncio.create_task(one.observe())
    await entered.wait()
    queued = asyncio.create_task(one.observe())
    assert (await asyncio.wait_for(two.observe(), 1)).text == "two"
    assert visited == ["one", "two"]
    release.set()
    await asyncio.gather(first, queued)
    assert visited == ["one", "two", "one"]


async def test_close_drains_late_new_tab_before_cleanup_and_prevents_navigation(scoped_browser):
    browser, infos = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["one"])
    entered, release = asyncio.Event(), asyncio.Event()

    async def create(source):
        entered.set()
        await release.wait()
        page = Page("late", "session-late")
        browser.pages[page.target] = page
        infos[page.target] = {"targetId": page.target, "type": "page", "openerId": source.target}
        browser._created({"targetInfo": infos[page.target]}, None)
        return page

    browser.new_tab = create
    browser.navigate = AsyncMock()
    creating = asyncio.create_task(backend.tabs("new", url="https://example.com"))
    await entered.wait()
    closing = asyncio.create_task(backend.close())
    await asyncio.sleep(0)
    assert not closing.done()
    with pytest.raises(BrowserError, match="closed"):
        await backend.screenshot()
    release.set()
    with pytest.raises(BrowserError, match="interrupted"):
        await creating
    await closing
    browser.navigate.assert_not_awaited()
    assert set(infos) == {"two", "external"}
    assert not backend.targets and not browser.owners


async def test_pause_drains_blocks_reads_and_resume_invalidates(scoped_browser):
    browser, _ = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["one"])
    entered, release = asyncio.Event(), asyncio.Event()

    async def observe(page):
        entered.set()
        await release.wait()
        page.revision = "before-takeover"
        return BrowserObservation("Page", page.revision, ())

    browser.observe = observe
    observing = asyncio.create_task(backend.observe())
    await entered.wait()
    pausing = asyncio.create_task(backend.pause())
    await asyncio.sleep(0)
    assert not pausing.done()
    for operation in (backend.screenshot, backend.downloads, lambda: backend.tabs("list")):
        with pytest.raises(BrowserHumanControl):
            await operation()
    with pytest.raises(BrowserError, match="drain"):
        backend.resume()
    release.set()
    await asyncio.gather(observing, pausing)
    assert not backend.page.revision
    waiter = asyncio.create_task(backend.wait_for_handback())
    await asyncio.sleep(0)
    assert not waiter.done()
    backend.resume()
    await waiter
    with pytest.raises(BrowserError, match="Observe"):
        await backend.act("press", value="Enter")


async def test_disconnect_wakes_empty_scope_and_preserves_remote_tabs(scoped_browser):
    browser, infos = scoped_browser
    backend = CdpBrowserBackend(browser)
    await backend.pause()
    waiter = asyncio.create_task(backend.wait_for_handback())
    await asyncio.sleep(0)
    await browser.close()
    with pytest.raises(BrowserError, match="disconnected"):
        await waiter
    assert set(infos) == {"one", "two", "external"}
    assert not browser.pages
    browser.client.send_raw.assert_not_awaited()


async def test_live_revocation_checks_reads_and_wait_completion(scoped_browser):
    browser, _ = scoped_browser
    allowed = True

    def check():
        if not allowed:
            raise BrowserError("Host access revoked")

    backend = CdpBrowserBackend(browser, browser.pages["one"], check_access=check)
    await backend.pause()
    allowed = False
    backend.resume()
    for operation in (backend.observe, backend.downloads, backend.wait_for_handback):
        with pytest.raises(BrowserError, match="revoked"):
            await operation()
    browser.client.send_raw.assert_not_awaited()


async def test_exact_target_reattachment_requires_fresh_observation(scoped_browser):
    browser, _ = scoped_browser
    backend = CdpBrowserBackend(browser)
    await backend.attach("one")
    assert backend.targets == {"one"}
    assert backend.page.requires_observation
    with pytest.raises(BrowserError, match="Observe"):
        await backend.act("press", value="Enter")


async def test_failed_cleanup_retains_membership_for_retry(scoped_browser):
    browser, infos = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["one"])
    original = browser.client.send_raw.side_effect

    async def fail(method, params, session):
        if method == "Target.closeTarget":
            raise RuntimeError("unavailable")
        return await original(method, params, session)

    browser.client.send_raw.side_effect = fail
    with pytest.raises(BrowserError, match="retry cleanup"):
        await backend.close()
    assert backend.targets == {"one"}
    browser.client.send_raw.side_effect = original
    await backend.close()
    assert "one" not in infos
