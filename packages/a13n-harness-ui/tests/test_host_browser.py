from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness.capabilities.browser.cdp import Page
from a13n_harness_ui.browser import Binding, BrowserAccessRequest, BrowserViewportRequest, HostBrowsers
from a13n_harness_ui.errors import HarnessUiError

pytestmark = pytest.mark.anyio


@pytest.fixture
def browsers(tmp_path):
    host = HostBrowsers(root=tmp_path, available=True, authorize=AsyncMock())
    host.runtime = AsyncMock()
    host.desktop = AsyncMock()
    host.desktop.desktop.xrandr = "/usr/bin/xrandr"
    host.desktop.control_socket = "/private/desktop.sock"
    host.desktop.readonly_socket = "/private/readonly.sock"
    host.desktop.password = "control-password"
    host.desktop.readonly_password = "view-password"
    host.focus = AsyncMock()
    host.layout = AsyncMock()
    for name in ("thread-a", "thread-b"):
        page = Page(name, "session-" + name)
        host.bindings[name] = Binding(status="ready", page=page)
        host.owners[page.target] = name
    host.runtime.pages = {item.page.target: item.page for item in host.bindings.values()}
    return host


async def test_viewer_admission_is_readonly_and_tickets_are_single_use(browsers, monkeypatch):
    identity = browsers.state("thread-a").session_id
    access = await browsers.access("thread-a", BrowserAccessRequest(session_id=identity))
    assert not access.control and access.password == "view-password"
    reader = AsyncMock()
    writer = Mock(wait_closed=AsyncMock())
    connect = AsyncMock(return_value=(reader, writer))
    monkeypatch.setattr(asyncio, "open_unix_connection", connect)
    assert await browsers.connect("thread-a", access.ticket) == (reader, writer)
    connect.assert_awaited_once_with("/private/readonly.sock")
    with pytest.raises(HarnessUiError, match="expired"):
        await browsers.connect("thread-a", access.ticket)


async def test_tickets_reject_other_threads_expiry_and_authority_changes(browsers):
    identity = browsers.state("thread-a").session_id
    access = await browsers.access("thread-a", BrowserAccessRequest(session_id=identity))
    with pytest.raises(HarnessUiError, match="expired"):
        await browsers.connect("thread-b", access.ticket)
    access = await browsers.access("thread-a", BrowserAccessRequest(session_id=identity))
    claim = browsers.tickets[access.ticket]
    browsers.tickets[access.ticket] = (*claim[:4], time.monotonic() - 1)
    with pytest.raises(HarnessUiError, match="expired"):
        await browsers.connect("thread-a", access.ticket)
    access = await browsers.access("thread-a", BrowserAccessRequest(session_id=identity))
    await browsers.control("thread-a", identity, human=True)
    with pytest.raises(HarnessUiError, match="expired"):
        await browsers.connect("thread-a", access.ticket)


async def test_control_revokes_native_connections_and_old_tokens(browsers):
    identity = browsers.state("thread-a").session_id
    viewer = Mock()
    browsers.connections[viewer] = "thread-a"
    control = await browsers.control("thread-a", identity, human=True)
    viewer.close.assert_called()
    access = await browsers.access(
        "thread-a", BrowserAccessRequest(session_id=identity, control_token=control.control_token)
    )
    assert access.control and access.password == "control-password"
    await browsers.control("thread-a", identity, human=False)
    with pytest.raises(HarnessUiError, match="revoked"):
        await browsers.access(
            "thread-a", BrowserAccessRequest(session_id=identity, control_token=control.control_token)
        )
    assert all(page.requires_observation for page in browsers.runtime.pages.values())


async def test_losing_controller_revokes_input_without_resuming_agent(browsers):
    identity = browsers.state("thread-a").session_id
    await browsers.control("thread-a", identity, human=True)
    writer = Mock()
    browsers.controller = writer
    browsers.connections[writer] = "thread-a"
    await browsers.disconnect(writer)
    assert not browsers.control_token and browsers.human_thread == "thread-a"
    assert not browsers.released.is_set()


async def test_takeover_settles_operations_without_waiting_for_the_entire_agent_task(browsers):
    finished = asyncio.Event()
    release = asyncio.Event()

    async def agent():
        backend = browsers.backend("thread-b", browsers.state("thread-b").session_id)
        async with backend.operation():
            await release.wait()
        finished.set()
        await asyncio.Event().wait()

    agent_task = asyncio.create_task(agent())
    await asyncio.sleep(0)
    control = asyncio.create_task(browsers.control("thread-a", browsers.state("thread-a").session_id, human=True))
    await asyncio.sleep(0)
    assert not control.done()
    release.set()
    await asyncio.wait_for(control, 1)
    assert finished.is_set() and not agent_task.done()
    agent_task.cancel()
    await asyncio.gather(agent_task, return_exceptions=True)


async def test_stop_retires_only_its_thread_and_preserves_shared_runtime(browsers):
    first, other = browsers.state("thread-a"), browsers.state("thread-b")
    runtime = browsers.runtime
    page = browsers.bindings["thread-a"].page
    stopped = await browsers.stop("thread-a", first.session_id)
    assert stopped.status == "stopped" and stopped.session_id != first.session_id
    assert browsers.runtime is runtime
    assert browsers.state("thread-b") == other
    runtime.close_page.assert_awaited_once_with(page)
    with pytest.raises(HarnessUiError, match="session changed"):
        browsers.checked("thread-a", first.session_id)


async def test_page_operations_in_distinct_threads_can_overlap(browsers):
    async with browsers.backend("thread-a", browsers.state("thread-a").session_id).operation():
        async with asyncio.timeout(1):
            async with browsers.backend("thread-b", browsers.state("thread-b").session_id).operation():
                assert len(browsers.operations) == 2


async def test_handback_waiter_wakes_on_local_stop_without_replaying(browsers):
    identity = browsers.state("thread-b").session_id
    await browsers.control("thread-a", browsers.state("thread-a").session_id, human=True)
    pending = asyncio.create_task(browsers.backend("thread-b", identity).wait_for_handback())
    await asyncio.sleep(0)
    await browsers.stop("thread-b", identity)
    with pytest.raises(HarnessUiError, match="session changed"):
        await asyncio.wait_for(pending, 1)
    assert browsers.human_thread == "thread-a"


async def test_stopped_binding_cannot_dispatch_a_queued_action(browsers):
    identity = browsers.state("thread-a").session_id
    page = browsers.bindings["thread-a"].page
    await page.lock.acquire()
    pending = asyncio.create_task(browsers.backend("thread-a", identity).observe())
    await asyncio.sleep(0)
    stopping = asyncio.create_task(browsers.stop("thread-a", identity))
    await asyncio.sleep(0)
    page.lock.release()
    await stopping
    with pytest.raises(HarnessUiError, match="session changed"):
        await pending
    browsers.runtime.observe.assert_not_awaited()


async def test_viewport_rechecks_authority_after_serialized_control_transition(browsers):
    identity = browsers.state("thread-a").session_id
    control = await browsers.control("thread-a", identity, human=True)
    await browsers.control_lock.acquire()
    release = asyncio.create_task(browsers.control("thread-a", identity, human=False))
    resize = asyncio.create_task(
        browsers.resize(
            "thread-a", BrowserViewportRequest(session_id=identity, control_token=control.control_token, preset="phone")
        )
    )
    await asyncio.sleep(0)
    browsers.control_lock.release()
    await release
    with pytest.raises(HarnessUiError, match="Take control"):
        await resize
    browsers.layout.assert_not_awaited()


async def test_shutdown_waits_for_profile_flush_before_disconnecting_and_stopping_display(browsers):
    events = []

    def event(name):
        events.append(name)

    runtime = browsers.runtime
    runtime.close_browser.side_effect = lambda: event("browser-close")
    runtime.close.side_effect = lambda: event("disconnect")
    process = Mock(returncode=None)
    process.wait = AsyncMock(side_effect=lambda: event("process-exit"))
    browsers.process = process
    browsers.desktop.close.side_effect = lambda: event("display-close")
    await browsers.close_runtime()
    assert events == ["browser-close", "process-exit", "disconnect", "display-close"]
    process.terminate.assert_not_called()


def test_shared_profile_is_always_under_the_data_root(tmp_path):
    from a13n_harness_ui.configuration.models import BrowserConfiguration

    # Additive configuration fields are retained but do not redirect profile storage.
    configuration = BrowserConfiguration.model_validate({"profile_root": "/old/custom/profile"})
    host = HostBrowsers(root=tmp_path / "browsers", available=True, authorize=AsyncMock(), configuration=configuration)
    assert "profile_root" not in BrowserConfiguration.model_fields
    assert host.root == tmp_path / "browsers" / "profile"


async def test_every_added_tool_respects_global_human_takeover(browsers):
    from a13n_harness.capabilities.browser import BrowserHumanControl

    backend = browsers.backend("thread-a", browsers.state("thread-a").session_id)
    browsers.human_thread = "thread-b"
    calls = (
        backend.tabs("list"),
        backend.scroll(x=0, y=500),
        backend.dialog("inspect"),
        backend.upload("ref", ("/private/file",), revision="revision"),
        backend.downloads(),
    )
    for operation in calls:
        with pytest.raises(BrowserHumanControl):
            await operation
    browsers.runtime.act.assert_not_awaited()


async def test_tab_operations_reject_cross_thread_targets(browsers):
    backend = browsers.backend("thread-a", browsers.state("thread-a").session_id)
    for action in ("select", "close"):
        with pytest.raises(HarnessUiError, match="does not belong"):
            await backend.tabs(action, target="thread-b")
    browsers.runtime.activate.assert_not_awaited()
    browsers.runtime.close_page.assert_not_awaited()
