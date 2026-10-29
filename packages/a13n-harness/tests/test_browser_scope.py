from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness.capabilities.browser import BrowserError, BrowserHumanControl, BrowserObservation
from a13n_harness.capabilities.browser.actions import Press
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

    async def observe(page, query=None):
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

    async def observe(page, query=None):
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
    assert "Observe" in await backend.interact(Press(kind="press", key="Enter"))


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
    assert "Observe" in await backend.interact(Press(kind="press", key="Enter"))


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


@pytest.mark.parametrize("operation", ["navigate", "wait"])
async def test_readiness_deadline_includes_scope_admission(scoped_browser, operation):
    import json

    from a13n_harness.capabilities.browser.actions import LoadCondition

    browser, _ = scoped_browser
    scope = CdpBrowserBackend(browser, browser.pages["one"])
    async with scope._lock:
        async with asyncio.timeout(1):
            result = (
                await scope.navigate("about:blank", timeout=0.01)
                if operation == "navigate"
                else await scope.wait(LoadCondition(kind="load"), timeout=0.01)
            )
    assert json.loads(result) == {"verification": "timed_out", "dispatch": "not_dispatched"}
    browser.client.send_raw.assert_not_awaited()


async def test_close_prunes_only_its_download_history_including_closed_popups(scoped_browser):
    browser, _ = scoped_browser
    one = CdpBrowserBackend(browser, browser.pages["one"])
    two = CdpBrowserBackend(browser, browser.pages["two"])
    one._claim("gone-popup")
    one._removed("gone-popup")
    browser.download_records = {
        key: {"page": target, "state": "completed"} for key, target in (("a", "one"), ("b", "gone-popup"), ("c", "two"))
    }
    browser.download_omitted = {"one": 1, "gone-popup": 2, "two": 3}
    await one.close()
    assert browser.download_records == {"c": {"page": "two", "state": "completed"}}
    assert browser.download_omitted == {"two": 3}
    assert not one._download_targets
    assert two in browser.scopes
    await one.close()
    assert "c" in browser.download_records


@pytest.mark.parametrize("previously_owned", [False, True])
async def test_empty_scope_new_window_only_replaces_previous_ownership(scoped_browser, previously_owned):
    browser, infos = scoped_browser
    scope = CdpBrowserBackend(browser, browser.pages["one"] if previously_owned else None)
    if previously_owned:
        await scope.tabs("close", target="one")
    page = Page("new", "new-session")

    async def create(**kwargs):
        browser.pages[page.target] = page
        infos[page.target] = {"targetId": page.target, "type": "page"}
        return page

    browser.create = AsyncMock(side_effect=create)
    assert not await scope.tabs("list")
    await scope.tabs("new")
    browser.create.assert_awaited_once_with(new_window=previously_owned)
    assert scope.page is page


async def test_global_access_does_not_claim_existing_tabs_or_change_other_selection(scoped_browser):
    browser, infos = scoped_browser
    owner = CdpBrowserBackend(browser, browser.pages["one"])
    global_scope = CdpBrowserBackend(browser, tab_access="global")
    infos["worker"] = {"targetId": "worker", "type": "service_worker"}
    assert {tab.target for tab in await global_scope.tabs("list")} == {"one", "two", "external"}
    await global_scope.tabs("select", target="one")
    await global_scope.attach("external")
    assert global_scope.page.target == "external"
    assert owner.page.target == "one"
    assert not global_scope.targets
    assert browser.owners == {"one": owner}
    with pytest.raises(BrowserError, match="accessible"):
        await global_scope.tabs("select", target="worker")
    with pytest.raises(BrowserError, match="no longer exists"):
        await global_scope.attach("worker")
    await global_scope.close()
    assert set(infos) == {"one", "two", "external", "worker"}
    assert owner.targets == {"one"}


@pytest.mark.parametrize("event", [True, False])
async def test_global_selected_external_target_loss_requires_explicit_selection(scoped_browser, event):
    browser, infos = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["external"], tab_access="global")
    infos.pop("external")
    if event:
        browser._destroyed({"targetId": "external"}, None)
    tabs = await backend.tabs("list")
    assert {tab.target for tab in tabs} == {"one", "two"}
    assert not any(tab.selected for tab in tabs)
    with pytest.raises(BrowserError, match="No tabs"):
        await backend.observe()
    with pytest.raises(BrowserError, match="no longer exists"):
        await backend.attach("external")
    await backend.tabs("select", target="two")
    assert backend.page.target == "two"
    assert not backend.targets


async def test_global_new_tab_and_popup_ownership_are_separate_from_borrowed_opener(scoped_browser):
    browser, infos = scoped_browser
    owner = CdpBrowserBackend(browser, browser.pages["one"])
    backend = CdpBrowserBackend(browser, browser.pages["one"], tab_access="global")
    page = Page("new", "new-session")

    async def create(**kwargs):
        browser.pages[page.target] = page
        infos[page.target] = {"targetId": page.target, "type": "page"}
        return page

    browser.create = AsyncMock(side_effect=create)
    browser.new_tab = AsyncMock()
    await backend.tabs("new")
    browser.create.assert_awaited_once_with(new_window=False)
    browser.new_tab.assert_not_awaited()
    for popup, opener in (("owned-popup", "one"), ("global-popup", "new"), ("user-popup", "external")):
        infos[popup] = {"targetId": popup, "type": "page", "openerId": opener}
        browser._created({"targetInfo": infos[popup]}, None)
    assert owner.targets == {"one", "owned-popup"}
    assert backend.targets == {"new", "global-popup"}
    assert "user-popup" not in browser.owners
    await backend.close()
    assert set(infos) == {"one", "two", "external", "owned-popup", "user-popup"}


async def test_shared_page_switch_expires_references_and_requires_own_observation(scoped_browser):
    from a13n_harness.capabilities.browser.cdp import Frame, Node

    browser, _ = scoped_browser
    owner = CdpBrowserBackend(browser, browser.pages["one"])
    shared = CdpBrowserBackend(browser, browser.pages["one"], tab_access="global")
    page = owner.page
    frame = Frame("root", page.session)
    browser.semantic = AsyncMock(return_value=([frame], []))
    first = await owner.observe()
    page.elements["old"] = Node(1, frame.identity, frame.session)
    page.visual = object()
    second = await shared.observe()
    assert first.revision != second.revision
    assert "old" not in page.elements and page.visual is None
    # Another binding's observation cannot authorize untargeted keyboard input.
    assert "Observe" in await owner.interact(Press(kind="press", key="Enter"))
    browser.client.send_raw.assert_not_awaited()
    third = await owner.observe()
    assert third.revision != second.revision
    assert not page.requires_observation


@pytest.mark.parametrize("operation", ["observe", "close"])
async def test_shared_page_waiter_rechecks_pause_before_dispatch(scoped_browser, operation):
    browser, _ = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["one"], tab_access="global")
    browser.observe = AsyncMock(return_value=BrowserObservation("page", "fresh", ()))
    async with backend.page.lock:
        pending = asyncio.create_task(
            backend.observe() if operation == "observe" else backend.tabs("close", target="one")
        )
        # Wait until the binding has admitted the call and is waiting for the page.
        while not backend._lock.locked():
            await asyncio.sleep(0)
        pausing = asyncio.create_task(backend.pause())
        await asyncio.sleep(0)
    with pytest.raises(BrowserHumanControl):
        await pending
    await pausing
    browser.observe.assert_not_awaited()
    assert all(call.args[0] != "Target.closeTarget" for call in browser.client.send_raw.await_args_list)


async def test_owner_cleanup_drains_global_page_operation_and_updates_all_bindings(scoped_browser):
    browser, infos = scoped_browser
    owner = CdpBrowserBackend(browser, browser.pages["one"])
    shared = CdpBrowserBackend(browser, browser.pages["one"], tab_access="global")
    entered, release = asyncio.Event(), asyncio.Event()

    async def read(page, kind, **kwargs):
        entered.set()
        await release.wait()
        return "read"

    browser.read = read
    reading = asyncio.create_task(shared.read("text"))
    await entered.wait()
    closing = asyncio.create_task(owner.close())
    await asyncio.sleep(0)
    assert "one" in infos and not closing.done()
    release.set()
    assert await reading == "read"
    await closing
    assert "one" not in infos
    assert not owner.targets and not shared.targets
    assert not any(tab.selected for tab in await shared.tabs("list"))


async def test_global_explicit_close_updates_owner_but_cleanup_leaves_borrowed_tabs(scoped_browser):
    browser, infos = scoped_browser
    owner = CdpBrowserBackend(browser, browser.pages["one"])
    backend = CdpBrowserBackend(browser, tab_access="global")
    await backend.tabs("close", target="one")
    assert not owner.targets
    await backend.close()
    assert set(infos) == {"two", "external"}


async def test_global_download_visibility_does_not_transfer_retention_or_cleanup(scoped_browser):
    import json

    browser, _ = scoped_browser
    owner = CdpBrowserBackend(browser, browser.pages["one"])
    backend = CdpBrowserBackend(browser, tab_access="global")
    browser.download_directory = "/downloads"
    browser.download_records = {
        "a": {"page": "one", "state": "completed", "suggestedFilename": "owned.txt"},
        "b": {"page": "external", "state": "completed", "suggestedFilename": "external.txt"},
    }
    browser.download_omitted = {"one": 1, "external": 2}
    assert len(json.loads(await owner.downloads())["downloads"]) == 1
    global_downloads = json.loads(await backend.downloads())
    assert len(global_downloads["downloads"]) == 2 and global_downloads["omitted"] == 3
    await backend.close()
    assert set(browser.download_records) == {"a", "b"}
    await owner.close()
    assert set(browser.download_records) == {"b"}


@pytest.mark.parametrize("interrupt", ["pause", "close"])
async def test_global_late_created_target_is_owned_even_when_interrupted(scoped_browser, interrupt):
    browser, infos = scoped_browser
    backend = CdpBrowserBackend(browser, browser.pages["external"], tab_access="global")
    entered, release = asyncio.Event(), asyncio.Event()

    async def create(**kwargs):
        entered.set()
        await release.wait()
        page = Page("late-global", "late-global-session")
        browser.pages[page.target] = page
        infos[page.target] = {"targetId": page.target, "type": "page"}
        return page

    browser.create = create
    browser.navigate = AsyncMock()
    creating = asyncio.create_task(backend.tabs("new", url="https://example.com"))
    await entered.wait()
    stopping = asyncio.create_task(backend.pause() if interrupt == "pause" else backend.close())
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(BrowserError, match="interrupted"):
        await creating
    await stopping
    browser.navigate.assert_not_awaited()
    if interrupt == "pause":
        assert backend.targets == {"late-global"}
        backend.resume()
        await backend.close()
    assert set(infos) == {"one", "two", "external"}
    assert not backend.targets


async def test_new_tab_preserves_other_pages_in_use_by_global_binding(scoped_browser):
    browser, infos = scoped_browser
    owner = CdpBrowserBackend(browser, browser.pages["one"])
    await owner.attach("two")
    shared = CdpBrowserBackend(browser, browser.pages["one"], tab_access="global")

    async def new_tab(source):
        page = Page("new", "new-session")
        browser.pages[page.target] = page
        infos[page.target] = {"targetId": page.target, "type": "page"}
        return page

    browser.new_tab = new_tab
    async with shared._page_operation(shared.page):
        shared.page.revision = "in-use"
        await owner.tabs("new")
        assert shared.page.revision == "in-use"
    assert owner.page.target == "new" and owner.page.requires_observation
    assert owner.targets == {"one", "two", "new"}


@pytest.mark.parametrize("operation", ["select", "attach"])
async def test_global_selection_waits_for_admitted_shared_page_input(scoped_browser, monkeypatch, operation):
    import json

    from a13n_harness.capabilities.browser import interaction
    from a13n_harness.capabilities.browser.actions import Ref, TypeText
    from a13n_harness.capabilities.browser.cdp import Frame, Node

    browser, _ = scoped_browser
    owner = CdpBrowserBackend(browser, browser.pages["one"])
    shared = CdpBrowserBackend(browser, tab_access="global")
    page = owner.page
    page.revision = "observed"
    page.frames["root"] = Frame("root", page.session)
    page.elements["input"] = Node(1, "root", page.session)
    entered, release = asyncio.Event(), asyncio.Event()
    typed = []

    async def focus(browser, page, target, revision, **kwargs):
        browser.reference(page, target, revision)

    async def press(browser, page, key, **kwargs):
        typed.append(key)
        if key == "a":
            entered.set()
            await release.wait()

    monkeypatch.setattr(interaction, "focus", focus)
    monkeypatch.setattr(interaction, "press", press)
    monkeypatch.setattr(interaction, "state", AsyncMock(return_value={}))
    typing = asyncio.create_task(
        owner.interact(TypeText(kind="type", target=Ref(kind="ref", ref="input"), text="abc"), revision="observed")
    )
    await asyncio.wait_for(entered.wait(), 1)
    selecting = asyncio.create_task(
        shared.tabs("select", target="one") if operation == "select" else shared.attach("one")
    )
    while not shared._lock.locked() and not selecting.done():
        await asyncio.sleep(0)
    still_typing = not selecting.done() and page.revision == "observed"
    release.set()
    result = await typing
    await selecting
    assert still_typing
    assert typed == ["a", "b", "c"] and json.loads(result)["dispatch"] == "dispatched"
    assert page.requires_observation and not page.revision
    assert owner.page is shared.page and not shared.targets
