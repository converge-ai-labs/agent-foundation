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
async def test_handback_admits_exact_human_tabs_without_stealing_other_scopes(browsers, close_original):
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
    await browsers.control("thread-a", binding.identity, human=False)
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
async def test_chromium_sandbox_requires_explicit_startup_opt_in(tmp_path, monkeypatch, no_sandbox, platform):
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


async def test_native_clipboard_requires_controller_and_handback_drains_transfer(browsers):
    from a13n_harness_ui.browser import BrowserClipboardRequest
    from a13n_harness_ui.browser_macos import MacDesktop

    browsers.desktop = MacDesktop()
    entered, finish = asyncio.Event(), asyncio.Event()

    async def transfer(text):
        entered.set()
        await finish.wait()
        return text

    browsers.desktop.clipboard = AsyncMock(side_effect=transfer)
    identity = browsers.state("thread-a").session_id
    control = await browsers.control("thread-a", identity, human=True)
    request = BrowserClipboardRequest(session_id=identity, control_token=control.control_token, text="你好 🚀")
    with pytest.raises(HarnessUiError, match="controlling viewer"):
        await browsers.clipboard("thread-a", request)
    viewer = Mock(is_closing=Mock(return_value=False), wait_closed=AsyncMock())
    browsers.controller = viewer
    browsers.connections[viewer] = "thread-a"
    transfer_task = asyncio.create_task(browsers.clipboard("thread-a", request))
    await entered.wait()
    handback = asyncio.create_task(browsers.control("thread-a", identity, human=False))
    await asyncio.sleep(0)
    assert not handback.done() and not browsers.released.is_set()
    finish.set()
    assert (await transfer_task).text == "你好 🚀"
    await handback
    viewer.wait_closed.assert_awaited_once()
    assert not browsers.connections and browsers.released.is_set()
    with pytest.raises(HarnessUiError, match="Take control"):
        await browsers.clipboard("thread-a", request)
    browsers.desktop.clipboard.assert_awaited_once()


async def test_macos_layout_keeps_physical_resolution(browsers):
    from a13n_harness_ui.browser_macos import MacDesktop

    browsers.desktop = MacDesktop()
    browsers.runtime.window = AsyncMock(return_value={"windowId": 42})
    browsers.runtime.window_bounds = AsyncMock()
    item = browsers.bindings["thread-a"]
    await HostBrowsers.layout(browsers, item.page, item)
    assert not browsers.state("thread-a").resize_available
    assert browsers.runtime.window_bounds.await_args_list[-1].args == (42, {"windowState": "maximized"})
