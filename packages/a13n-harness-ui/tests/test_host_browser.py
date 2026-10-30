from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness.capabilities.browser import BrowserError
from a13n_harness.capabilities.browser.actions import Scroll, TextCondition
from a13n_harness.capabilities.browser.cdp import Chromium, Page
from a13n_harness.capabilities.browser.scope import CdpBrowserBackend
from a13n_harness_ui.browser import Binding, BrowserAccessRequest, BrowserViewportRequest, HostBrowsers
from a13n_harness_ui.browser_display import VncDesktop
from a13n_harness_ui.browser_setup import VncExecutable
from a13n_harness_ui.errors import HarnessUiError

pytestmark = pytest.mark.anyio


@pytest.fixture
def browsers(tmp_path):
    host = HostBrowsers(root=tmp_path, available=True, authorize=AsyncMock())
    runtime = host.runtime = Chromium(Mock())
    runtime.closed = host.page_closed
    runtime.opened = host.opened

    async def send(method, params=None, **kwargs):
        assert method == "Target.getTargets"
        return {"targetInfos": [{"type": "page", "targetId": target} for target in runtime.pages]}

    async def close_target(target):
        runtime._destroyed({"targetId": target}, None)

    runtime.send = AsyncMock(side_effect=send)
    runtime.close_target = AsyncMock(side_effect=close_target)
    runtime.close_page = AsyncMock(side_effect=lambda page: close_target(page.target))
    runtime.close_browser = AsyncMock()
    runtime.close = AsyncMock()
    runtime.observe = AsyncMock()
    runtime.navigate = AsyncMock()
    runtime.evaluate = AsyncMock(return_value={})
    runtime.release_keys = AsyncMock()
    runtime.activate = AsyncMock()
    host.desktop = VncDesktop(VncExecutable(path="/usr/bin/Xvnc", source="custom", xrandr="/usr/bin/xrandr"))
    host.desktop.settle_input = AsyncMock()
    host.desktop.close = AsyncMock()
    host.desktop.resize = AsyncMock()
    host.desktop.control_socket = "/private/desktop.sock"
    host.desktop.readonly_socket = "/private/readonly.sock"
    host.desktop.password = "control-password"
    host.desktop.readonly_password = "view-password"
    host.focus = AsyncMock()
    host.layout = AsyncMock()
    for name in ("thread-a", "thread-b"):
        page = Page(name, "session-" + name)
        runtime.pages[page.target] = page
        host.bindings[name] = Binding(status="ready", scope=CdpBrowserBackend(runtime, page))
    return host


@pytest.mark.parametrize(
    "snapshots,expected",
    [
        ([OSError("unavailable"), {456}], None),
        ([{456}, OSError("unavailable")], None),
        ([{456}, {456, 789}], 789),
        ([set(), {789}], 789),
        ([set(), {456, 789}], None),
    ],
)
async def test_macos_preview_binds_only_a_proven_new_window(browsers, monkeypatch, snapshots, expected):
    from a13n_harness_ui import browser as module
    from a13n_harness_ui.browser_macos import MacDesktop

    browsers.desktop = MacDesktop()
    browsers.process = Mock(pid=123)
    browsers.launch = AsyncMock()
    page = Page("new-target", "new-session")
    browsers.runtime.pages[page.target] = page
    browsers.runtime.create = AsyncMock(return_value=page)
    item = browsers.bindings["new-thread"] = Binding()
    discover = Mock(side_effect=snapshots)
    monkeypatch.setattr(module, "window_ids", discover)
    state = await browsers.start("new-thread", item.identity)
    assert state.status == "ready"
    assert item.preview_window == expected
    assert item.page is page
    if isinstance(snapshots[0], OSError):
        discover.assert_called_once_with(123)
    browsers.process = None


async def test_viewer_admission_is_readonly_and_tickets_are_single_use(browsers, monkeypatch):
    identity = browsers.state("thread-a").session_id
    access = await browsers.access("thread-a", BrowserAccessRequest(session_id=identity))
    assert not access.control and access.password == "view-password"
    reader = AsyncMock()
    writer = Mock(wait_closed=AsyncMock())
    connect = AsyncMock(return_value=(reader, writer))
    monkeypatch.setattr(asyncio, "open_unix_connection", connect)
    connection = await browsers.connect("thread-a", access.ticket)
    assert (connection.reader, connection.writer) == (reader, writer)
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
    viewer = Mock(wait_closed=AsyncMock())
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
    writer = Mock(wait_closed=AsyncMock())
    browsers.controller = writer
    browsers.connections[writer] = "thread-a"
    await browsers.disconnect(writer)
    assert not browsers.control_token and browsers.human_thread == "thread-a"
    assert not browsers.released.is_set()


async def test_reconnect_control_renews_authority_without_resuming_agent(browsers):
    identity = browsers.state("thread-a").session_id
    first = await browsers.control("thread-a", identity, human=True)
    ticket = await browsers.access(
        "thread-a", BrowserAccessRequest(session_id=identity, control_token=first.control_token)
    )
    # A failed handshake may leave an unclaimed token; explicit retry replaces it.
    second = await browsers.control("thread-a", identity, human=True)
    assert second.control_token and second.control_token != first.control_token
    assert not browsers.released.is_set()
    assert all(item.scope._paused for item in browsers.bindings.values())
    with pytest.raises(HarnessUiError, match="expired"):
        await browsers.connect("thread-a", ticket.ticket)
    with pytest.raises(HarnessUiError, match="revoked"):
        await browsers.access("thread-a", BrowserAccessRequest(session_id=identity, control_token=first.control_token))
    writer = Mock(wait_closed=AsyncMock())
    browsers.controller = writer
    browsers.connections[writer] = "thread-a"
    third = await browsers.control("thread-a", identity, human=True)
    writer.close.assert_called()
    writer.wait_closed.assert_awaited_once()
    assert writer not in browsers.connections
    assert third.control_token and third.control_token != second.control_token
    assert browsers.human_thread == "thread-a" and not browsers.released.is_set()
    replacement = Mock(wait_closed=AsyncMock())
    browsers.controller = replacement
    browsers.connections[replacement] = "thread-a"
    # Cleanup from the replaced viewer cannot revoke the new controller.
    await browsers.disconnect(writer)
    assert browsers.controller is replacement
    assert browsers.control_token == third.control_token


@pytest.mark.parametrize("destination", ["thread-a", "thread-b"])
async def test_transfer_settles_old_controller_before_issuing_authority(browsers, destination):
    identity = browsers.state("thread-a").session_id
    first = await browsers.control("thread-a", identity, human=True)
    ticket = await browsers.access(
        "thread-a", BrowserAccessRequest(session_id=identity, control_token=first.control_token)
    )
    closing = asyncio.Event()
    settled = asyncio.Event()

    async def wait_closed():
        closing.set()
        await settled.wait()

    writer = Mock(wait_closed=AsyncMock(side_effect=wait_closed))
    browsers.controller = writer
    browsers.connections[writer] = "thread-a"
    transfer = asyncio.create_task(browsers.control(destination, browsers.state(destination).session_id, human=True))
    await asyncio.wait_for(closing.wait(), 1)
    assert not transfer.done() and not browsers.control_token
    assert browsers.controller is None and not browsers.released.is_set()
    assert browsers.human_thread == "thread-a"
    assert all(item.scope._paused for item in browsers.bindings.values())
    with pytest.raises(HarnessUiError, match="expired"):
        await browsers.connect("thread-a", ticket.ticket)
    settled.set()
    second = await asyncio.wait_for(transfer, 1)
    assert browsers.human_thread == destination
    browsers.focus.assert_awaited_with(browsers.bindings[destination].page)
    assert second.control_token and second.control_token != first.control_token
    with pytest.raises(HarnessUiError, match="revoked"):
        await browsers.access("thread-a", BrowserAccessRequest(session_id=identity, control_token=first.control_token))


async def test_competing_transfers_leave_only_the_latest_authority(browsers):
    identity = browsers.state("thread-a").session_id
    await browsers.control("thread-a", identity, human=True)
    first, second = await asyncio.gather(
        browsers.control("thread-a", identity, human=True),
        browsers.control("thread-a", identity, human=True),
    )
    assert first.control_token != second.control_token == browsers.control_token
    assert not browsers.released.is_set()
    with pytest.raises(HarnessUiError, match="revoked"):
        await browsers.access("thread-a", BrowserAccessRequest(session_id=identity, control_token=first.control_token))


async def test_failed_control_reconnect_preserves_pause(browsers):
    identity = browsers.state("thread-a").session_id
    await browsers.control("thread-a", identity, human=True)
    browsers.focus.side_effect = RuntimeError("Focus failed")
    with pytest.raises(RuntimeError, match="Focus failed"):
        await browsers.control("thread-a", identity, human=True)
    assert browsers.human_thread == "thread-a" and not browsers.released.is_set()
    assert not browsers.control_token
    assert all(item.scope._paused for item in browsers.bindings.values())
    browsers.focus.side_effect = None
    await browsers.control("thread-a", identity, human=False)
    assert browsers.released.is_set()


@pytest.mark.parametrize("mac", [False, True])
@pytest.mark.parametrize("empty", [False, True])
async def test_global_handback_from_another_thread(browsers, mac, empty):
    from a13n_harness_ui.browser_macos import MacDesktop

    if mac:
        browsers.desktop = MacDesktop()
    first = browsers.state("thread-a").session_id
    other = browsers.bindings["thread-b"]
    if empty:
        await other.scope.close()
        other.scope = CdpBrowserBackend(browsers.runtime)
    await browsers.control("thread-a", first, human=True)
    assert all(browsers.state(thread).control == "human" for thread in browsers.bindings)
    pending = asyncio.create_task(browsers.backend("thread-a", first).wait_for_handback())
    result = await browsers.control("thread-b", other.identity, human=False)
    await asyncio.wait_for(pending, 1)
    assert result.state.control == "agent" and not result.control_token
    assert browsers.released.is_set()
    assert all(not item.scope._paused for item in browsers.bindings.values())
    assert all(page.requires_observation for page in browsers.runtime.pages.values())
    browsers.authorize.assert_awaited_with("thread-b")


async def test_handback_allows_enabling_another_thread_during_scope_sync(browsers, monkeypatch):
    from a13n_harness_ui import browser as module

    monkeypatch.setattr(module, "require_browser", AsyncMock())
    entered, proceed = asyncio.Event(), asyncio.Event()
    original = browsers.sync_selected_tab

    async def sync(thread):
        if thread == "thread-a":
            entered.set()
            await proceed.wait()
        await original(thread)

    browsers.sync_selected_tab = AsyncMock(side_effect=sync)
    identity = browsers.state("thread-a").session_id
    await browsers.control("thread-a", identity, human=True)
    handback = asyncio.create_task(browsers.control("thread-a", identity, human=False))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await browsers.enable("thread-c")
    finally:
        proceed.set()
        await asyncio.wait_for(handback, 1)
    assert not browsers.runtime_failed
    assert browsers.released.is_set() and browsers.handback.available
    assert browsers.state("thread-c").control == "agent"
    assert all(browsers.bindings[thread].status == "ready" for thread in ("thread-a", "thread-b"))
    assert browsers.sync_selected_tab.await_count == 2


async def test_browser_failure_discards_pending_handback_notices(browsers):
    from anyio import create_task_group, wait_all_tasks_blocked

    browsers.handback.deliver = AsyncMock(return_value=True)
    async with create_task_group() as group:
        browsers.handback.start(group)
        await browsers.control("thread-a", browsers.state("thread-a").session_id, human=True)
        browsers.handback.blocked("thread-b")
        browsers.failed("Browser process exited")
        assert not browsers.handback.available
        assert browsers.state("thread-b").status == "failed"
        browsers.handback.released()
        await wait_all_tasks_blocked()
        browsers.handback.deliver.assert_not_awaited()
        browsers.handback.stop()


async def test_failed_cross_thread_transfer_preserves_global_pause(browsers):
    await browsers.control("thread-a", browsers.state("thread-a").session_id, human=True)
    browsers.focus.side_effect = RuntimeError("Focus failed")
    with pytest.raises(RuntimeError, match="Focus failed"):
        await browsers.control("thread-b", browsers.state("thread-b").session_id, human=True)
    assert browsers.human_thread == "thread-a" and not browsers.released.is_set()
    assert not browsers.control_token
    assert all(item.scope._paused for item in browsers.bindings.values())
    await browsers.control("thread-b", browsers.state("thread-b").session_id, human=False)
    assert browsers.released.is_set()


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
    runtime.close_target.assert_awaited_once_with(page.target)
    with pytest.raises(HarnessUiError, match="session changed"):
        browsers.checked("thread-a", first.session_id)


@pytest.mark.parametrize("action", ["stop", "reset"])
async def test_stop_settles_inflight_tab_creation(browsers, action):
    binding = browsers.bindings["thread-a"]
    original = binding.page
    other = browsers.state("thread-b")
    runtime = browsers.runtime
    created = Page("late-tab", "late-session")
    replacement = Page("replacement", "replacement-session")
    reached = asyncio.Event()
    resume = asyncio.Event()

    async def pause():
        reached.set()
        await resume.wait()

    async def new_tab(page):
        await pause()
        runtime.pages[created.target] = created
        return created

    async def create(**kwargs):
        runtime.pages[replacement.target] = replacement
        return replacement

    runtime.new_tab = AsyncMock(side_effect=new_tab)
    runtime.create = AsyncMock(side_effect=create)
    browsers.launch = AsyncMock()
    pending = asyncio.create_task(
        browsers.backend("thread-a", binding.identity).tabs("new", url="https://example.com/")
    )
    async with asyncio.timeout(2):
        await reached.wait()
        stopping = asyncio.create_task(getattr(browsers, action)("thread-a", binding.identity))
        await binding.closed.wait()
        assert not stopping.done()
        # Stopping this Thread must not wait for or block another Thread's tools.
        await browsers.backend("thread-b", other.session_id).observe()
        resume.set()
        with pytest.raises(BrowserError, match="interrupted"):
            await pending
        result = await stopping

    assert {call.args[0] for call in runtime.close_target.await_args_list} == {original.target, created.target}
    assert created.target not in runtime.pages and created.target not in browsers.owners
    assert original.target not in runtime.pages and original.target not in browsers.owners
    runtime.navigate.assert_not_awaited()
    assert browsers.state("thread-b") == other
    assert browsers.runtime is runtime
    assert result.status == ("ready" if action == "reset" else "stopped")
    if action == "reset":
        assert browsers.bindings["thread-a"].page is replacement
        assert browsers.owners[replacement.target] == "thread-a"


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
    entered, resume = asyncio.Event(), asyncio.Event()

    async def observe(page, query):
        entered.set()
        await resume.wait()

    browsers.runtime.observe.side_effect = observe
    backend = browsers.backend("thread-a", identity)
    admitted = asyncio.create_task(backend.observe())
    await entered.wait()
    queued = asyncio.create_task(backend.observe())
    await asyncio.sleep(0)
    stopping = asyncio.create_task(browsers.stop("thread-a", identity))
    await browsers.bindings["thread-a"].closed.wait()
    resume.set()
    await admitted
    await stopping
    with pytest.raises(BrowserError, match="closed"):
        await queued
    browsers.runtime.observe.assert_awaited_once()


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

    async def close_target(target):
        event("page-close:" + target)
        runtime._destroyed({"targetId": target}, None)

    runtime.close_target.side_effect = close_target
    runtime.close_browser.side_effect = lambda: event("browser-close")
    runtime.close.side_effect = lambda: event("disconnect")
    process = Mock(returncode=None)
    process.wait = AsyncMock(side_effect=lambda: event("process-exit"))
    browsers.process = process
    browsers.desktop.close.side_effect = lambda: event("display-close")
    viewer = Mock(
        close=Mock(side_effect=lambda: event("viewer-close")),
        wait_closed=AsyncMock(side_effect=lambda: event("viewer-settled")),
    )
    browsers.connections[viewer] = "thread-a"
    browsers.controller = viewer
    await browsers.close_runtime()
    assert events == [
        "viewer-close",
        "viewer-settled",
        "page-close:thread-a",
        "page-close:thread-b",
        "browser-close",
        "process-exit",
        "disconnect",
        "display-close",
    ]
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
        backend.observe(),
        backend.read("text"),
        backend.screenshot(),
        backend.navigate("https://example.com"),
        backend.wait(TextCondition(kind="text", text="Ready")),
        backend.tabs("list"),
        backend.interact(Scroll(kind="scroll", y=500)),
        backend.dialog("inspect"),
        backend.upload("ref", ("/private/file",), revision="revision"),
        backend.downloads(),
    )
    for operation in calls:
        with pytest.raises(BrowserHumanControl):
            await operation
    browsers.runtime.send.assert_not_awaited()


async def test_tab_operations_reject_cross_thread_targets(browsers):
    backend = browsers.backend("thread-a", browsers.state("thread-a").session_id)
    for action in ("select", "close"):
        with pytest.raises(BrowserError, match="not owned"):
            await backend.tabs(action, target="thread-b")
    browsers.runtime.activate.assert_not_awaited()
    browsers.runtime.close_target.assert_not_awaited()


@pytest.mark.parametrize("close_original", [False, True])
@pytest.mark.parametrize("transfer", [False, True])
async def test_handback_admits_exact_human_tabs_without_stealing_other_scopes(browsers, close_original, transfer):
    binding = browsers.bindings["thread-a"]
    runtime = browsers.runtime
    original = binding.page
    unowned = Page("preexisting", "preexisting-session")
    runtime.pages[unowned.target] = unowned
    await browsers.control("thread-a", binding.identity, human=True)
    manual = Page("manual", "manual-session")
    runtime.pages[manual.target] = manual
    runtime._created({"targetInfo": {"type": "page", "targetId": manual.target}}, None)
    popup = Page("popup-b", "popup-session")
    runtime.pages[popup.target] = popup
    runtime._created({"targetInfo": {"type": "page", "targetId": popup.target, "openerId": "thread-b"}}, None)
    runtime.attach = AsyncMock(side_effect=lambda target: runtime.pages[target])
    runtime.evaluate.side_effect = lambda page, expression: {"visible": page is manual, "focused": page is manual}
    if close_original:
        runtime._destroyed({"targetId": original.target}, None)
        assert binding.status == "ready"
    if transfer:
        await browsers.control("thread-b", browsers.state("thread-b").session_id, human=True)
        later = Page("manual-b", "manual-b-session")
        runtime.pages[later.target] = later
        runtime._created({"targetInfo": {"type": "page", "targetId": later.target}}, None)
    await browsers.control("thread-b", browsers.state("thread-b").session_id, human=False)
    if transfer:
        assert browsers.owners[later.target] == "thread-b"
    assert browsers.owners[manual.target] == "thread-a"
    assert browsers.owners[popup.target] == "thread-b"
    assert unowned.target not in browsers.owners
    assert binding.page is manual
    assert manual.requires_observation
    assert not browsers.human_tabs


async def test_shutdown_drains_scoped_operations_before_closing_browser(browsers):
    entered, resume = asyncio.Event(), asyncio.Event()

    async def observe(page, query):
        entered.set()
        await resume.wait()

    browsers.runtime.observe.side_effect = observe
    runtime = browsers.runtime
    browsers.process = Mock(returncode=None, wait=AsyncMock(return_value=0))
    identity = browsers.state("thread-a").session_id
    pending = asyncio.create_task(browsers.backend("thread-a", identity).observe())
    await entered.wait()
    closing = asyncio.create_task(browsers.close())
    await browsers.bindings["thread-a"].closed.wait()
    runtime.close_browser.assert_not_awaited()
    resume.set()
    await pending
    await closing
    runtime.close_browser.assert_awaited_once()
    assert not runtime.owners and not runtime.scopes


@pytest.mark.parametrize("kind", ["tigervnc", "x11vnc"])
async def test_private_x11_desktop_does_not_inherit_host_wayland(monkeypatch, kind):
    import a13n_harness_ui.browser_display as module
    from a13n_harness_ui.browser_setup import VncExecutable

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-host")
    monkeypatch.setenv("SWAYSOCK", "/host/sway.sock")
    monkeypatch.setenv("DISPLAY", ":host")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    process = Mock(stdout=Mock(readline=AsyncMock(return_value=b"123\n")))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(module, "spawn", spawn)
    monkeypatch.setattr(module, "terminate", AsyncMock())
    monkeypatch.setattr(module, "wait_socket", AsyncMock())
    desktop = module.VncDesktop(
        VncExecutable(
            path="/prepared/vnc",
            source="custom",
            kind=kind,
            display_server="/prepared/Xvfb",
            window_manager="/prepared/openbox",
        )
    )
    try:
        await desktop.start()
        assert desktop.env["DISPLAY"] == ":123"
        assert desktop.env["XDG_SESSION_TYPE"] == "x11"
        assert "WAYLAND_DISPLAY" not in desktop.env and "SWAYSOCK" not in desktop.env
        for call in spawn.await_args_list:
            assert call.kwargs["env"] == desktop.env
    finally:
        await desktop.close()


@pytest.mark.parametrize("no_sandbox", [False, True])
@pytest.mark.parametrize("platform", ["linux", "macos"])
async def test_chromium_launch_storage_and_sandbox_policy(tmp_path, monkeypatch, no_sandbox, platform):
    import a13n_harness_ui.browser as module
    from a13n_harness_ui.browser_setup import BrowserExecutable, BrowserSetup, VncExecutable

    host = HostBrowsers(root=tmp_path, available=True, authorize=AsyncMock(), no_sandbox=no_sandbox)
    desktop = AsyncMock()
    desktop.env = {}
    desktop.chromium_arguments = ("--ozone-platform=x11",) if platform == "linux" else ()
    preparation = BrowserSetup(
        platform=platform,
        supported=True,
        ready=True,
        browser=BrowserExecutable(name="Chromium", path="/prepared/chromium", source="custom"),
        vnc=VncExecutable(path="/prepared/Xvnc", source="custom"),
    )
    monkeypatch.setattr(module, "require_browser", AsyncMock(return_value=preparation))
    monkeypatch.setattr(module, "VncDesktop" if platform == "linux" else "MacDesktop", Mock(return_value=desktop))
    spawn = AsyncMock(return_value=Mock(returncode=1))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(RuntimeError, match="exited during startup"):
        await host.launch()
    assert ("--no-sandbox" in spawn.call_args.args) is no_sandbox
    assert ("--password-store=basic" in spawn.call_args.args) is (platform == "linux")
    assert ("--ozone-platform=x11" in spawn.call_args.args) is (platform == "linux")
    desktop.close.assert_awaited_once()
    assert host.desktop is None and host.process is None


async def test_start_retry_closes_failed_scope_before_replacing_binding(browsers):
    runtime = browsers.runtime
    browsers.bindings["new-thread"] = Binding()
    failed = browsers.bindings["new-thread"]
    pages = [Page("first-start", "first-session"), Page("retry", "retry-session")]

    async def create(**kwargs):
        page = pages.pop(0)
        runtime.pages[page.target] = page
        return page

    runtime.create = AsyncMock(side_effect=create)
    browsers.launch = AsyncMock()
    browsers.layout.side_effect = [TimeoutError("layout failed"), None]
    with pytest.raises(HarnessUiError, match="startup failed"):
        await browsers.start("new-thread", failed.identity)
    scope = failed.scope
    assert scope is not None
    # A failed cleanup must retain ownership for a subsequent explicit retry.
    close_target = runtime.close_target.side_effect
    runtime.close_target.side_effect = BrowserError("close failed")
    with pytest.raises(BrowserError, match="could not be closed"):
        await browsers.start("new-thread", failed.identity, explicit=True)
    assert browsers.bindings["new-thread"] is failed and failed.scope is scope
    runtime.close_target.side_effect = close_target
    state = await browsers.start("new-thread", failed.identity, explicit=True)
    assert scope not in runtime.scopes and "first-start" not in runtime.pages
    await browsers.stop("new-thread", state.session_id)
    assert "retry" not in runtime.pages
    assert set(runtime.pages) == {"thread-a", "thread-b"}


async def test_failed_display_is_not_reused_even_while_chromium_is_alive(browsers, monkeypatch):
    import a13n_harness_ui.browser as module

    browsers.process = Mock(returncode=None, wait=AsyncMock(return_value=0))
    runtime, desktop = browsers.runtime, browsers.desktop
    browsers.failed("VNC exited")
    preparation = AsyncMock(side_effect=RuntimeError("new preparation reached"))
    monkeypatch.setattr(module, "require_browser", preparation)
    with pytest.raises(RuntimeError, match="new preparation reached"):
        await browsers.launch()
    runtime.close.assert_awaited_once()
    desktop.close.assert_awaited_once()
    preparation.assert_awaited_once()
    assert browsers.runtime is None


async def test_viewer_connect_rechecks_stop_after_socket_open(browsers, monkeypatch):
    binding = browsers.bindings["thread-a"]
    access = await browsers.access("thread-a", BrowserAccessRequest(session_id=binding.identity))
    opening, connected, observing, release = (asyncio.Event() for _ in range(4))
    writer = Mock(wait_closed=AsyncMock())

    async def open_socket(path):
        opening.set()
        await connected.wait()
        return AsyncMock(), writer

    async def observe(page, query):
        observing.set()
        await release.wait()

    monkeypatch.setattr(asyncio, "open_unix_connection", open_socket)
    browsers.runtime.observe.side_effect = observe
    operation = asyncio.create_task(browsers.backend("thread-a", binding.identity).observe())
    await observing.wait()
    viewer = asyncio.create_task(browsers.connect("thread-a", access.ticket))
    await opening.wait()
    stopping = asyncio.create_task(browsers.stop("thread-a", binding.identity))
    await binding.closed.wait()
    connected.set()
    try:
        with pytest.raises(HarnessUiError, match="revoked"):
            await asyncio.wait_for(viewer, 1)
        writer.close.assert_called_once()
        assert writer not in browsers.connections
    finally:
        release.set()
        await operation
        await stopping


@pytest.mark.parametrize("human", [False, True])
async def test_last_tab_close_keeps_binding_reusable(browsers, human):
    item = browsers.bindings["thread-a"]
    scope = item.scope
    backend = browsers.backend("thread-a", item.identity)
    if human:
        await browsers.control("thread-a", item.identity, human=True)
        await browsers.runtime.close_target(item.page.target)
        assert browsers.human_thread == "thread-a" and not browsers.released.is_set()
        await browsers.control("thread-a", item.identity, human=False)
    else:
        assert await backend.tabs("close", target=item.page.target) == ()
    assert browsers.binding("thread-a") == item.identity
    assert item.status == "ready" and not item.closed.is_set()
    assert await backend.tabs("list") == ()
    with pytest.raises(BrowserError, match="No tabs remain"):
        await backend.observe()
    await browsers.start("thread-a", item.identity, explicit=True)
    assert item.scope is scope
    page = Page("new", "new-session")

    async def create(**kwargs):
        browsers.runtime.pages[page.target] = page
        return page

    browsers.runtime.create = AsyncMock(side_effect=create)
    await backend.tabs("new")
    assert item.scope is scope and item.page is page
    assert browsers.owners[page.target] == "thread-a"


async def test_takeover_focus_failure_rolls_back_all_scopes(browsers):
    item = browsers.bindings["thread-a"]
    browsers.focus.side_effect = BrowserError("focus failed")
    with pytest.raises(BrowserError, match="focus failed"):
        await browsers.control("thread-a", item.identity, human=True)
    assert not browsers.human_thread and browsers.released.is_set()
    assert not browsers.control_token and not browsers.tickets
    for thread, binding in browsers.bindings.items():
        await browsers.backend(thread, binding.identity).observe()
    browsers.desktop.settle_input.assert_awaited()


async def test_cancelled_takeover_drains_before_resuming(browsers):
    item = browsers.bindings["thread-b"]
    entered, finish = asyncio.Event(), asyncio.Event()

    async def observe(page, query):
        entered.set()
        await finish.wait()

    browsers.runtime.observe.side_effect = observe
    operation = asyncio.create_task(browsers.backend("thread-b", item.identity).observe())
    await entered.wait()
    takeover = asyncio.create_task(browsers.control("thread-a", browsers.binding("thread-a"), human=True))
    while not item.scope._paused:
        await asyncio.sleep(0)
    takeover.cancel()
    await asyncio.sleep(0)
    assert not takeover.done() and not browsers.released.is_set()
    finish.set()
    await operation
    with pytest.raises(asyncio.CancelledError):
        await takeover
    assert browsers.released.is_set() and not browsers.human_thread
    assert not item.scope._paused
    await browsers.backend("thread-b", item.identity).observe()


async def test_takeover_input_failure_requires_explicit_restart(browsers):
    browsers.desktop.settle_input.side_effect = RuntimeError("display gone")
    with pytest.raises(RuntimeError, match="display gone"):
        await browsers.control("thread-a", browsers.binding("thread-a"), human=True)
    assert browsers.runtime_failed and browsers.released.is_set()
    assert not browsers.human_thread and not browsers.control_token
    assert all(item.status == "failed" for item in browsers.bindings.values())


async def test_empty_binding_can_view_and_hand_back_without_replacement(browsers):
    item = browsers.bindings["thread-a"]
    browsers.runtime._destroyed({"targetId": item.page.target}, None)
    control = await browsers.control("thread-a", item.identity, human=True)
    access = await browsers.access(
        "thread-a",
        BrowserAccessRequest(
            session_id=item.identity,
            control_token=control.control_token,
        ),
    )
    assert access.control
    browsers.focus.assert_not_awaited()
    with pytest.raises(HarnessUiError, match="Open a tab"):
        await browsers.resize(
            "thread-a",
            BrowserViewportRequest(
                session_id=item.identity,
                control_token=control.control_token,
                preset="phone",
            ),
        )
    await browsers.control("thread-a", item.identity, human=False)
    assert browsers.released.is_set() and item.status == "ready"


async def test_pause_failure_waits_for_other_scope_before_retiring(browsers):
    item = browsers.bindings["thread-b"]
    entered, finish = asyncio.Event(), asyncio.Event()

    async def observe(page, query):
        entered.set()
        await finish.wait()

    browsers.runtime.observe.side_effect = observe
    browsers.bindings["thread-a"].scope.pause = AsyncMock(side_effect=BrowserError("scope closed"))
    operation = asyncio.create_task(browsers.backend("thread-b", item.identity).observe())
    await entered.wait()
    takeover = asyncio.create_task(browsers.control("thread-a", browsers.binding("thread-a"), human=True))
    while not item.scope._paused:
        await asyncio.sleep(0)
    assert not takeover.done() and not browsers.released.is_set()
    finish.set()
    await operation
    with pytest.raises(BrowserError, match="scope closed"):
        await takeover
    assert browsers.runtime_failed and browsers.released.is_set()
    assert all(item.status == "failed" for item in browsers.bindings.values())


async def test_failed_resize_preserves_saved_preset_and_invalidates_observation(browsers):
    item = browsers.bindings["thread-a"]
    item.viewport = "tablet"
    control = await browsers.control("thread-a", item.identity, human=True)
    browsers.layout.side_effect = BrowserError("resize failed")
    with pytest.raises(BrowserError, match="resize failed"):
        await browsers.resize(
            "thread-a",
            BrowserViewportRequest(session_id=item.identity, control_token=control.control_token, preset="phone"),
        )
    assert item.viewport == "tablet" and item.status == "ready"
    assert item.page.requires_observation


async def test_recreated_window_receives_saved_layout(browsers):
    item = browsers.bindings["thread-a"]
    item.viewport = "tablet"
    runtime = browsers.runtime
    runtime._destroyed({"targetId": item.page.target}, None)
    page = Page("replacement", "replacement-session")

    async def create(**kwargs):
        runtime.pages[page.target] = page
        return page

    runtime.create = AsyncMock(side_effect=create)
    await browsers.backend("thread-a", item.identity).tabs("new")
    browsers.layout.assert_awaited_once_with(page, item)
    assert item.viewport == "tablet" and item.status == "ready"


async def test_desktop_preparation_fits_requested_and_other_owned_windows(browsers):
    browsers.runtime.window = AsyncMock(return_value={"bounds": {"width": 1300, "height": 900}})
    await browsers.fit_desktop(776, 1115)
    browsers.desktop.resize.assert_awaited_once_with(1301, 1116)


@pytest.mark.parametrize("state", ["maximized", "fullscreen", "minimized"])
async def test_desktop_preparation_ignores_screen_managed_windows(browsers, state):
    screen = {"width": 1400, "height": 1200}

    async def window(page):
        bounds = (
            dict(screen, windowState=state)
            if page.target == "thread-b"
            else {"width": 776, "height": 1115, "windowState": "normal"}
        )
        return {"bounds": bounds}

    async def resize(width, height):
        screen.update(width=width, height=height)

    browsers.runtime.window = AsyncMock(side_effect=window)
    browsers.desktop.resize.side_effect = resize
    for _ in range(5):
        await browsers.fit_desktop(776, 1115)
        assert screen == {"width": 777, "height": 1116}


async def test_recreated_window_reports_creation_when_takeover_precedes_layout(browsers):
    item = browsers.bindings["thread-a"]
    runtime = browsers.runtime
    runtime._destroyed({"targetId": item.page.target}, None)
    page = Page("replacement", "replacement-session")
    created = asyncio.Event()

    async def create(**kwargs):
        runtime.pages[page.target] = page
        created.set()
        return page

    runtime.create = AsyncMock(side_effect=create)
    await browsers.control_lock.acquire()
    takeover = asyncio.create_task(browsers.control("thread-a", item.identity, human=True))
    await asyncio.sleep(0)
    creation = asyncio.create_task(browsers.backend("thread-a", item.identity).tabs("new"))
    await created.wait()
    browsers.control_lock.release()
    await takeover
    tabs = await creation
    assert [tab.target for tab in tabs] == [page.target]
    assert page.target in item.scope.targets
    assert browsers.human_thread == "thread-a"
    browsers.layout.assert_not_awaited()


async def test_macos_pause_never_grants_remote_input(browsers, monkeypatch):
    from a13n_harness_ui.browser_capture import WindowPreview
    from a13n_harness_ui.browser_macos import MacDesktop

    browsers.desktop = MacDesktop()
    browsers.process = Mock(pid=123)
    item = browsers.bindings["thread-a"]
    item.preview_window = 456
    control = await browsers.control("thread-a", item.identity, human=True)
    access = await browsers.access(
        "thread-a",
        BrowserAccessRequest(
            session_id=item.identity,
            control_token=control.control_token,
        ),
    )
    assert access.protocol == "jpeg" and not access.control and not access.password
    viewer = Mock(spec=WindowPreview, wait_closed=AsyncMock())
    start = AsyncMock(return_value=viewer)
    monkeypatch.setattr(WindowPreview, "start", start)
    assert await browsers.connect("thread-a", access.ticket) is viewer
    start.assert_awaited_once_with(123, 456)
    assert browsers.controller is None
    await browsers.control("thread-a", item.identity, human=False)
    viewer.close.assert_called()
    viewer.wait_closed.assert_awaited_once()
    assert item.page.requires_observation and browsers.released.is_set()


async def test_macos_missing_window_never_falls_back_to_another_window(browsers, monkeypatch):
    from a13n_harness_ui.browser_capture import WindowPreview
    from a13n_harness_ui.browser_macos import MacDesktop

    browsers.desktop = MacDesktop()
    browsers.process = Mock(pid=123)
    start = AsyncMock()
    monkeypatch.setattr(WindowPreview, "start", start)
    item = browsers.bindings["thread-a"]
    access = await browsers.access("thread-a", BrowserAccessRequest(session_id=item.identity))
    with pytest.raises(HarnessUiError, match="Window preview unavailable"):
        await browsers.connect("thread-a", access.ticket)
    start.assert_not_awaited()
    assert item.status == "ready"


async def test_macos_layout_keeps_physical_resolution(browsers):
    from a13n_harness_ui.browser_macos import MacDesktop

    browsers.desktop = MacDesktop()
    browsers.runtime.window = AsyncMock(return_value={"windowId": 42})
    browsers.runtime.window_bounds = AsyncMock()
    item = browsers.bindings["thread-a"]
    await HostBrowsers.layout(browsers, item.page, item)
    assert not browsers.state("thread-a").resize_available
    assert browsers.runtime.window_bounds.await_args_list[-1].args == (42, {"windowState": "maximized"})


async def test_auto_prepare_is_once_and_admission_allocates_no_pages(browsers):
    browsers.launch = AsyncMock()
    browsers.runtime.create = AsyncMock()
    await asyncio.gather(browsers.prepare(), browsers.prepare())
    first, second = await asyncio.gather(browsers.admit("new-thread"), browsers.admit("new-thread"))
    assert first == second and first
    browsers.launch.assert_awaited_once()
    browsers.runtime.create.assert_not_awaited()
    assert browsers.state("new-thread").page_count == 0
    assert browsers.state("new-thread").status == "ready"


@pytest.mark.parametrize("available,mode", [(False, "auto"), (True, "disabled")])
async def test_auto_policy_gates_preparation_and_admission(tmp_path, available, mode):
    from a13n_harness_ui.configuration.models import BrowserConfiguration

    host = HostBrowsers(
        root=tmp_path, available=available, authorize=AsyncMock(), configuration=BrowserConfiguration(mode=mode)
    )
    host.launch = AsyncMock()
    await host.prepare()
    assert await host.admit("thread") is None
    assert host.state("thread").status == "disabled"
    host.launch.assert_not_awaited()


async def test_failed_preparation_is_optional_and_explicit_open_retries(browsers):
    browsers.launch = AsyncMock(side_effect=BrowserError("Missing runtime"))
    await browsers.prepare()
    assert await browsers.admit("new-thread") is None
    assert browsers.state("new-thread").error == "Missing runtime"
    browsers.launch.assert_awaited_once()

    async def recover():
        browsers.runtime_failed = False

    browsers.launch = AsyncMock(side_effect=recover)
    page = Page("first", "first-session")
    browsers.runtime.pages[page.target] = page
    browsers.runtime.create = AsyncMock(return_value=page)
    opened = await browsers.open("new-thread")
    assert opened.status == "ready" and opened.page_count == 1
    assert browsers.startup_error is None
    browsers.runtime.create.assert_awaited_once()


@pytest.mark.parametrize("action", ["navigate", "new"])
async def test_close_pages_preserves_run_binding_and_can_reopen(browsers, action):
    identity = browsers.state("thread-a").session_id
    backend = browsers.backend("thread-a", identity)
    other = browsers.bindings["thread-b"].page
    closed = await browsers.close_pages("thread-a", identity)
    assert closed.session_id == identity and closed.page_count == 0
    assert not browsers.bindings["thread-a"].closed.is_set()
    assert browsers.bindings["thread-b"].page is other
    page = Page("replacement", "replacement-session")
    browsers.runtime.pages[page.target] = page
    browsers.runtime.create = AsyncMock(return_value=page)
    if action == "navigate":
        browsers.runtime.observe = AsyncMock(
            side_effect=lambda page, query=None: setattr(page, "requires_observation", False)
        )
        await backend.navigate("https://example.com")
        browsers.runtime.navigate.assert_awaited_once()
    else:
        await backend.tabs("new")
    assert browsers.state("thread-a").page_count == 1
    assert browsers.state("thread-a").session_id == identity


async def test_auto_admission_during_takeover_retains_handback_barrier(browsers):
    browsers.launch = AsyncMock()
    await browsers.control("thread-a", browsers.state("thread-a").session_id, human=True)
    identity = await browsers.admit("new-thread")
    assert identity
    backend = browsers.backend("new-thread", identity)
    with pytest.raises(HarnessUiError, match="user controls"):
        await backend.tabs("new")
    await browsers.control("thread-a", browsers.state("thread-a").session_id, human=False)
    assert await backend.tabs("list") == ()


async def test_handback_notifies_only_tool_blocked_threads_after_native_release(browsers):
    from functools import partial

    from a13n_harness.capabilities import BrowserUseCapability
    from anyio import create_task_group, wait_all_tasks_blocked

    a = browsers.bindings["thread-a"]
    b = browsers.bindings["thread-b"]
    delivered = []

    async def deliver(thread, prompt, current):
        assert current() and not browsers.human_thread
        assert browsers.released.is_set()
        assert not browsers.control_token
        assert b.page.requires_observation
        delivered.append(thread)
        return True

    browsers.handback.deliver = deliver
    async with create_task_group() as group:
        browsers.handback.start(group)
        await browsers.control("thread-a", a.identity, human=True)
        tools = (
            BrowserUseCapability(
                browsers.backend("thread-b", b.identity),
                on_human_control=partial(browsers.handback.blocked, "thread-b"),
            )
            .get_toolset()
            .tools
        )
        for tool in ("browser_observe", "browser_screenshot"):
            result = await tools[tool].function_schema.call({}, None)
            assert "notification is registered" in result
        assert not delivered
        await browsers.control("thread-b", b.identity, human=False)
        await wait_all_tasks_blocked()
        assert delivered == ["thread-b"]
        browsers.handback.stop()


async def test_control_lease_expires_after_controller_disconnect(browsers, monkeypatch):
    from a13n_harness_ui import browser as module

    monkeypatch.setattr(module, "CONTROL_LEASE_SECONDS", 0.05)
    identity = browsers.state("thread-a").session_id
    await browsers.control("thread-a", identity, human=True)
    writer = Mock(wait_closed=AsyncMock())
    browsers.controller = writer
    browsers.connections[writer] = "thread-a"
    await browsers.disconnect(writer)
    assert not browsers.control_token and not browsers.released.is_set()
    await asyncio.wait_for(browsers.released.wait(), 1)
    assert browsers.state("thread-a").control == "agent"
    assert browsers.state("thread-a").control_remaining_seconds is None


async def test_control_lease_renewal_and_late_request(browsers, monkeypatch):
    from a13n_harness_ui import browser as module

    monkeypatch.setattr(module, "CONTROL_LEASE_SECONDS", 0.12)
    identity = browsers.state("thread-a").session_id
    control = await browsers.control("thread-a", identity, human=True)
    request = BrowserAccessRequest(session_id=identity, control_token=control.control_token)
    await asyncio.sleep(0.07)
    renewed = await browsers.renew_control("thread-a", request)
    assert renewed.control_remaining_seconds > 0.1
    await asyncio.sleep(0.07)
    assert not browsers.released.is_set()
    await asyncio.wait_for(browsers.released.wait(), 1)
    with pytest.raises(HarnessUiError):
        await browsers.renew_control("thread-a", request)


async def test_expired_control_cannot_renew_before_timer_finishes(browsers):
    import time

    identity = browsers.state("thread-a").session_id
    control = await browsers.control("thread-a", identity, human=True)
    request = BrowserAccessRequest(session_id=identity, control_token=control.control_token)
    browsers.control_deadline = time.monotonic() - 1
    assert browsers.control_token == control.control_token
    assert browsers.lease_task and not browsers.lease_task.done()
    with pytest.raises(HarnessUiError, match="expired"):
        await browsers.renew_control("thread-a", request)
    assert browsers.control_token == control.control_token and not browsers.released.is_set()


async def test_old_episode_cannot_release_transferred_control(browsers):
    first = browsers.state("thread-a").session_id
    second = browsers.state("thread-b").session_id
    control = await browsers.control("thread-a", first, human=True)
    old_episode = browsers.control_episode
    await browsers.control("thread-b", second, human=True)
    await browsers.expire_control(old_episode)
    assert browsers.human_thread == "thread-b" and not browsers.released.is_set()
    with pytest.raises(HarnessUiError):
        await browsers.renew_control(
            "thread-a", BrowserAccessRequest(session_id=first, control_token=control.control_token)
        )
    await browsers.control("thread-b", second, human=False)
    assert browsers.released.is_set()


async def test_lease_expiry_keeps_barrier_during_input_drain(browsers, monkeypatch):
    from a13n_harness_ui import browser as module

    monkeypatch.setattr(module, "CONTROL_LEASE_SECONDS", 0.05)
    identity = browsers.state("thread-a").session_id
    await browsers.control("thread-a", identity, human=True)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def settle():
        entered.set()
        await finish.wait()

    browsers.desktop.settle_input = AsyncMock(side_effect=settle)
    await asyncio.wait_for(entered.wait(), 1)
    assert not browsers.released.is_set()
    state = browsers.state("thread-a")
    assert state.control == "human" and state.control_remaining_seconds == 0
    finish.set()
    await asyncio.wait_for(browsers.released.wait(), 1)


async def test_stale_handback_does_not_release_transferred_episode(browsers):
    first = browsers.state("thread-a").session_id
    second = browsers.state("thread-b").session_id
    old = (await browsers.control("thread-a", first, human=True)).state.control_episode
    await browsers.control("thread-b", second, human=True)
    with pytest.raises(HarnessUiError):
        await browsers.control("thread-a", first, human=False, episode=old)
    assert browsers.human_thread == "thread-b" and not browsers.released.is_set()
    current = browsers.state("thread-a").control_episode
    await browsers.control("thread-a", first, human=False, episode=current)
    assert browsers.released.is_set()


async def test_expiry_notifies_only_blocked_threads_after_handback(browsers, monkeypatch):
    from a13n_harness_ui import browser as module
    from a13n_harness_ui.browser_handback import HANDBACK
    from anyio import create_task_group

    monkeypatch.setattr(module, "CONTROL_LEASE_SECONDS", 0.05)
    delivered = []
    notice = asyncio.Event()

    async def deliver(thread, prompt, current):
        assert browsers.released.is_set() and current()
        delivered.append((thread, prompt))
        notice.set()
        return True

    browsers.handback.deliver = deliver
    async with create_task_group() as group:
        browsers.handback.start(group)
        await browsers.control("thread-a", browsers.state("thread-a").session_id, human=True)
        browsers.handback.blocked("thread-b")
        browsers.handback.blocked("thread-b")
        await asyncio.wait_for(notice.wait(), 1)
        assert delivered == [("thread-b", HANDBACK)]
        browsers.handback.stop()
