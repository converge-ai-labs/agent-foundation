from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, Mock, call

import pytest
from a13n_harness_ui import browser as module
from a13n_harness_ui.browser import (
    BrowserAccessRequest,
    BrowserViewportRequest,
    HostBrowsers,
    browser_url,
)
from a13n_harness_ui.browser_setup import BrowserExecutable, BrowserSetup
from a13n_harness_ui.errors import HarnessUiError

from .test_app import _settings, _write_configuration

pytestmark = pytest.mark.anyio


@pytest.fixture
async def browsers(tmp_path, monkeypatch):
    monkeypatch.setattr(
        module,
        "inspect_browser",
        AsyncMock(
            return_value=BrowserSetup(
                platform="linux",
                supported=True,
                ready=True,
                browser=BrowserExecutable(name="Chromium", path="/browser/chrome", source="playwright"),
            )
        ),
    )
    owner = HostBrowsers(root=tmp_path / "browsers", available=True, authorize=AsyncMock())
    yield owner
    await owner.close()


async def ready(browsers):
    state = await browsers.enable("thread-test")
    session = browsers.session("thread-test", state.session_id)
    session.page = Mock()
    session.page.is_closed.return_value = False
    session.page.screenshot = AsyncMock(return_value=b"picture")
    session.page.keyboard.press = AsyncMock()
    session.state = state.model_copy(update={"status": "ready"})
    return session, browsers.backend("thread-test", state.session_id)


async def test_takeover_blocks_reads_actions_and_requires_fresh_observation_on_handback(browsers):
    session, backend = await ready(browsers)
    assert await backend.screenshot() == b"picture"
    control = await browsers.control("thread-test", session.state.session_id, human=True)
    assert control.control_token
    for request in (backend.observe, backend.screenshot, lambda: backend.act("press", value="Enter")):
        with pytest.raises(HarnessUiError, match="user controls"):
            await request()
    assert session.page.screenshot.await_count == 1
    await browsers.control("thread-test", session.state.session_id, human=False)
    with pytest.raises(HarnessUiError, match="after handback"):
        await backend.act("press", value="Enter")
    session.requires_observation = False
    await backend.act("press", value="Enter")
    session.page.keyboard.press.assert_awaited_once_with("Enter")


async def test_takeover_waits_for_in_flight_and_blocks_queued_agent_call(browsers):
    session, backend = await ready(browsers)
    await session.lock.acquire()
    taking = asyncio.create_task(browsers.control("thread-test", session.state.session_id, human=True))
    await asyncio.sleep(0)
    assert session.state.control == "human"
    assert not taking.done()
    reading = asyncio.create_task(backend.screenshot())
    session.lock.release()
    await taking
    with pytest.raises(HarnessUiError, match="user controls"):
        await reading
    session.page.screenshot.assert_not_awaited()


async def test_access_tickets_expire_are_single_use_and_cannot_upgrade_read_only(browsers):
    session, _ = await ready(browsers)
    request = BrowserAccessRequest(session_id=session.state.session_id)
    first = await browsers.access("thread-test", request)
    assert not first.control
    with pytest.raises(HarnessUiError, match="expired"):
        await browsers.connect("another-thread", first.ticket)
    with pytest.raises(HarnessUiError, match="expired"):
        await browsers.connect("thread-test", first.ticket)
    second = await browsers.access("thread-test", request)
    old = browsers.tickets[second.ticket]
    browsers.tickets[second.ticket] = (*old[:4], time.monotonic() - 1)
    with pytest.raises(HarnessUiError, match="expired"):
        await browsers.connect("thread-test", second.ticket)
    with pytest.raises(HarnessUiError, match="revoked"):
        await browsers.access("thread-test", request.model_copy(update={"control_token": "invented"}))


async def test_handback_revokes_connections_and_outstanding_controller_ticket(browsers):
    session, _ = await ready(browsers)
    control = await browsers.control("thread-test", session.state.session_id, human=True)
    access = await browsers.access(
        "thread-test", BrowserAccessRequest(session_id=session.state.session_id, control_token=control.control_token)
    )
    writer = Mock()
    session.connections.add(writer)
    await browsers.control("thread-test", session.state.session_id, human=False)
    writer.close.assert_called_once()
    assert not session.control_token
    with pytest.raises(HarnessUiError, match="revoked"):
        await browsers.connect("thread-test", access.ticket)


async def test_access_waits_for_native_credential_rotation_before_issuing_a_ticket(browsers):
    session, _ = await ready(browsers)
    rotating, resume = asyncio.Event(), asyncio.Event()

    async def rotate():
        rotating.set()
        await resume.wait()
        session.password = "new-full"
        session.readonly_password = "new-view"

    session.rotate_passwords = AsyncMock(side_effect=rotate)
    taking = asyncio.create_task(browsers.control("thread-test", session.state.session_id, human=True))
    await rotating.wait()
    admission = asyncio.create_task(
        browsers.access("thread-test", BrowserAccessRequest(session_id=session.state.session_id))
    )
    await asyncio.sleep(0)
    assert not admission.done()
    resume.set()
    control = await taking
    assert control.control_token not in repr(control)
    readonly = await admission
    assert readonly.password == "new-view"
    writable = await browsers.access(
        "thread-test", BrowserAccessRequest(session_id=session.state.session_id, control_token=control.control_token)
    )
    assert writable.password == "new-full"
    assert "new-full" not in repr(writable)
    assert control.control_token not in repr(
        BrowserAccessRequest(session_id=session.state.session_id, control_token=control.control_token)
    )
    assert browsers.tickets[writable.ticket][2] == session.generation


async def test_stop_retires_queued_work_preserves_profile_and_new_binding(browsers, monkeypatch):
    session, backend = await ready(browsers)
    session.profile.mkdir(parents=True)
    (session.profile / "retained-login").write_text("fixture")
    session.targets["old"] = AsyncMock()
    stopped = await browsers.stop("thread-test", session.state.session_id)
    assert stopped.status == "stopped"
    with pytest.raises(HarnessUiError, match="session changed"):
        await backend.screenshot()
    assert (await browsers.enable("thread-test")).status == "stopped"
    monkeypatch.setattr(module.BrowserSession, "start", AsyncMock())
    new = await browsers.start("thread-test", session.state.session_id, explicit=True)
    assert new.session_id != session.state.session_id
    assert (session.profile / "retained-login").read_text() == "fixture"


async def test_host_checks_authority_on_every_operation(browsers):
    session, backend = await ready(browsers)
    browsers.authorize.side_effect = HarnessUiError(
        "Sandbox cannot use Host browser.", code="browser_full_control_required"
    )
    with pytest.raises(HarnessUiError, match="Sandbox"):
        await backend.screenshot()
    with pytest.raises(HarnessUiError, match="Sandbox"):
        await browsers.reset("thread-test", session.state.session_id)
    with pytest.raises(HarnessUiError, match="Sandbox"):
        await browsers.stop("thread-test", session.state.session_id)
    assert not session.retired
    await browsers.close()
    assert session.retired
    assert not browsers.sessions


async def test_concurrent_viewers_cannot_exceed_the_limit_while_connecting(browsers, monkeypatch):
    session, _ = await ready(browsers)
    session.connections.update(Mock() for _ in range(7))
    request = BrowserAccessRequest(session_id=session.state.session_id)
    first, second = await browsers.access("thread-test", request), await browsers.access("thread-test", request)
    opened, resume = asyncio.Event(), asyncio.Event()
    writers = [Mock(), Mock()]
    pending = 0

    async def connect(path):
        nonlocal pending
        writer = writers[pending]
        pending += 1
        if pending == 2:
            opened.set()
        await resume.wait()
        return Mock(), writer

    monkeypatch.setattr(module.asyncio, "open_unix_connection", connect)
    connections = [asyncio.create_task(browsers.connect("thread-test", item.ticket)) for item in (first, second)]
    await opened.wait()
    resume.set()
    results = await asyncio.gather(*connections, return_exceptions=True)
    assert sum(isinstance(result, HarnessUiError) and result.code == "browser_limit" for result in results) == 1
    assert len(session.connections) == 8
    assert sum(writer.close.call_count for writer in writers) == 1


async def test_shutdown_attempts_all_runtime_cleanups_after_one_fails(browsers):
    first = await browsers.enable("thread-first")
    second = await browsers.enable("thread-second")
    sessions = [
        browsers.session("thread-first", first.session_id),
        browsers.session("thread-second", second.session_id),
    ]
    for session in sessions:
        session.close = AsyncMock()
    sessions[1].close.side_effect = RuntimeError("Cleanup failed")
    with pytest.raises(RuntimeError, match="Cleanup failed"):
        await browsers.close()
    assert all(session.retired for session in sessions)
    for session in sessions:
        session.close.assert_awaited_once()
        if session.cleanup is not None:
            await session.cleanup
    assert not browsers.sessions


async def test_preparation_is_read_only_and_rechecks_authority(browsers):
    assert (await browsers.setup("thread-test")).ready
    assert not browsers.sessions
    browsers.authorize.assert_has_awaits([call("thread-test"), call("thread-test")])
    browsers.authorize.side_effect = [None, HarnessUiError("Authority changed", code="browser_full_control_required")]
    with pytest.raises(HarnessUiError, match="Authority changed"):
        await browsers.setup("thread-test")


async def test_enable_rejects_unprepared_host_without_creating_binding(browsers, monkeypatch):
    monkeypatch.setattr(
        module,
        "inspect_browser",
        AsyncMock(
            return_value=BrowserSetup(platform="linux", supported=True, ready=False, message="Install components")
        ),
    )
    with pytest.raises(HarnessUiError, match="Install components") as raised:
        await browsers.enable("thread-test")
    assert raised.value.code == "browser_runtime_not_ready"
    assert not browsers.sessions
    assert not browsers.root.exists()


async def test_installation_is_host_scoped_idempotent_and_does_not_enable_a_browser(browsers, monkeypatch):
    preparation = BrowserSetup(platform="linux", supported=True, ready=False, installable=True)
    monkeypatch.setattr(module, "inspect_browser", AsyncMock(return_value=preparation))
    started, release = asyncio.Event(), asyncio.Event()

    async def install(*args):
        started.set()
        await release.wait()

    operation = AsyncMock(side_effect=install)
    monkeypatch.setattr(browsers.installer, "install", operation)
    result = await browsers.install("thread-a")
    await started.wait()
    assert result.installation.status == "installing"
    assert (await browsers.install("thread-b")).installation.status == "installing"
    assert operation.await_count == 1
    assert not browsers.sessions
    release.set()
    await browsers.installer.task


async def test_installation_rejects_missing_system_privileges(browsers, monkeypatch):
    preparation = BrowserSetup(
        platform="linux", supported=True, ready=False, installable=False, install_message="Administrator required"
    )
    monkeypatch.setattr(module, "inspect_browser", AsyncMock(return_value=preparation))
    with pytest.raises(HarnessUiError, match="Administrator required"):
        await browsers.install("thread-a")
    assert browsers.installer.task is None


@pytest.mark.parametrize("connected", [False, True])
async def test_context_cleanup_tolerates_only_an_already_disconnected_browser(connected):
    from playwright.async_api import Error

    context = Mock()
    context.close = AsyncMock(side_effect=Error("Close response lost"))
    context.browser.is_connected.return_value = connected
    if connected:
        with pytest.raises(Error, match="Close response lost"):
            await module.close_context(context)
    else:
        await module.close_context(context)


async def test_webui_opt_in_is_authenticated_and_default_shell_use_has_no_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(
        module,
        "inspect_browser",
        AsyncMock(
            return_value=BrowserSetup(
                platform="linux",
                supported=True,
                ready=True,
                browser=BrowserExecutable(name="Chromium", path="/browser/chrome", source="playwright"),
            )
        ),
    )
    from contextlib import asynccontextmanager

    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.share_models import ShareCreate
    from a13n_harness_ui.webui import create_webui
    from httpx import ASGITransport, AsyncClient

    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(
        _settings(tmp_path / "state"), configuration_path=root, host_mode="webui", share_computer=True
    ) as app:
        thread = await app.create_thread()
        assert app.browsers().sessions == {}

        @asynccontextmanager
        async def borrowed():
            yield app

        server = create_webui(borrowed, api_key="test-key")
        async with (
            server.router.lifespan_context(server),
            AsyncClient(transport=ASGITransport(app=server), base_url="http://127.0.0.1") as client,
        ):
            url = f"/api/threads/{thread.thread_id}/browser/enable"
            setup_url = f"/api/threads/{thread.thread_id}/browser/setup"
            assert (await client.get(setup_url)).status_code == 401
            assert (await client.post(f"/api/threads/{thread.thread_id}/browser/install")).status_code == 401
            preparation = await client.get(setup_url, headers={"Authorization": "Bearer test-key"})
            assert preparation.status_code == 200
            assert preparation.json()["ready"]
            assert not app.browsers().sessions
            for permission in ("view", "prompt"):
                share = await app.sharing.create(
                    ShareCreate(scope="thread", target_id=thread.thread_id, permission=permission)
                )
                headers = {"Authorization": f"Bearer {share.key}"}
                prefix = f"/api/shared/{share.grant.share_id}"
                for method, suffix in (("GET", "/setup"), ("POST", "/install"), ("POST", "/access")):
                    path = f"/threads/{thread.thread_id}/browser{suffix}"
                    assert (await client.request(method, "/api" + path, headers=headers)).status_code == 401
                    assert (await client.request(method, prefix + path, headers=headers)).status_code == (
                        404 if method == "GET" else 405
                    )
            assert not app.browsers().sessions and not app.browsers().installer.running
            assert (await client.post(url)).status_code == 401
            reset_url = f"/api/threads/{thread.thread_id}/browser/reset"
            assert (await client.post(reset_url, json={"session_id": "unknown"})).status_code == 401
            response = await client.post(url, headers={"Authorization": "Bearer test-key"})
            assert response.status_code == 200
            assert response.json()["status"] == "enabled"
            assert app.browsers().sessions[thread.thread_id].page is None
            binding = response.json()["session_id"]
            reset = AsyncMock(return_value=app.browsers().state(thread.thread_id))
            monkeypatch.setattr(app.browsers(), "reset", reset)
            assert (
                await client.post(reset_url, headers={"Authorization": "Bearer test-key"}, json={"session_id": binding})
            ).status_code == 200
            reset.assert_awaited_once_with(thread.thread_id, binding)
            monkeypatch.setattr(
                module, "require_browser", AsyncMock(side_effect=module.failure("Missing browser", "runtime_not_ready"))
            )
            assert (
                await client.post(
                    f"/api/threads/{thread.thread_id}/browser/start",
                    headers={"Authorization": "Bearer test-key"},
                    json={"session_id": binding},
                )
            ).status_code == 503
    async with open_harness_ui_app(_settings(tmp_path / "ordinary"), configuration_path=root) as ordinary:
        assert not ordinary.browser_available
        with pytest.raises(HarnessUiError, match="sharing"):
            ordinary.browsers()


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "javascript:alert(1)", "https://user:secret@example.com", "https://"]
)
async def test_navigation_rejects_non_web_and_credential_urls(url):
    with pytest.raises(HarnessUiError):
        browser_url(url)


async def test_lost_controller_revokes_input_but_does_not_resume_agent(browsers):
    session, backend = await ready(browsers)
    await browsers.control("thread-test", session.state.session_id, human=True)
    writer = Mock()
    session.connections.add(writer)
    session.controller_connection = writer
    await browsers.disconnect(session, writer)
    assert not session.control_token
    assert session.state.control == "human"
    with pytest.raises(HarnessUiError, match="user controls"):
        await backend.screenshot()
    await browsers.control("thread-test", session.state.session_id, human=False)
    assert session.state.control == "agent"


async def test_takeover_of_unstarted_browser_does_not_leave_a_false_human_barrier(browsers):
    state = await browsers.enable("thread-test")
    with pytest.raises(HarnessUiError, match="Start the browser"):
        await browsers.control("thread-test", state.session_id, human=True)
    assert browsers.state("thread-test").control == "agent"


async def test_closing_a_tab_preserves_a_remaining_browser_tab_and_expires_refs(browsers):
    session, _ = await ready(browsers)
    session.page.is_closed.return_value = True
    replacement = Mock()
    replacement.is_closed.return_value = False
    session.context = Mock(pages=[replacement])
    session.revision = "old-tab"
    assert browsers.state("thread-test").status == "ready"
    assert session.page is replacement
    assert session.requires_observation
    assert session.revision == ""


async def test_stopping_the_browser_wakes_a_handback_waiter_without_replaying(browsers):
    session, backend = await ready(browsers)
    await browsers.control("thread-test", session.state.session_id, human=True)
    waiting = asyncio.create_task(backend.wait_for_handback())
    await asyncio.sleep(0)
    assert not waiting.done()
    await browsers.stop("thread-test", session.state.session_id)
    with pytest.raises(HarnessUiError, match="session changed"):
        await asyncio.wait_for(waiting, 1)


async def test_runtime_failure_revokes_before_cleanup_and_explicit_reopen_replaces_binding(browsers, monkeypatch):
    session, backend = await ready(browsers)
    binding = session.state.session_id
    session.profile.mkdir(parents=True)
    (session.profile / "saved-profile").write_text("retained")
    await browsers.control("thread-test", binding, human=True)
    ticket = await browsers.access(
        "thread-test", BrowserAccessRequest(session_id=binding, control_token=session.control_token)
    )
    writer = Mock()
    session.connections.add(writer)
    cleanup = AsyncMock()
    session.resources.push_async_callback(cleanup)
    waiting = asyncio.create_task(backend.wait_for_handback())
    await asyncio.sleep(0)
    await session.lock.acquire()
    try:
        session.end("Chromium closed.")
        assert browsers.state("thread-test").status == "stopped"
        assert browsers.binding("thread-test") is None
        assert not session.control_token
        writer.close.assert_called()
        cleanup.assert_not_awaited()
        with pytest.raises(HarnessUiError, match="session changed"):
            await asyncio.wait_for(waiting, 1)
        with pytest.raises(HarnessUiError, match="session changed"):
            await browsers.connect("thread-test", ticket.ticket)
    finally:
        session.lock.release()
    await session.close()
    cleanup.assert_awaited_once()
    with pytest.raises(HarnessUiError, match="session changed"):
        await backend.act("navigate", value="https://example.com")

    async def start(replacement):
        replacement.state = replacement.state.model_copy(update={"status": "ready"})

    monkeypatch.setattr(module.BrowserSession, "start", start)
    new = await browsers.start("thread-test", binding, explicit=True)
    replacement = browsers.session("thread-test", new.session_id)
    assert new.session_id != binding
    assert replacement.requires_observation
    assert (replacement.profile / "saved-profile").read_text() == "retained"
    with pytest.raises(HarnessUiError, match="session changed"):
        await backend.screenshot()


async def test_closed_last_page_fails_without_waiting_for_a_viewer(browsers):
    session, _ = await ready(browsers)
    session.page.is_closed.return_value = True
    session.context = Mock(pages=[])
    session.page_closed(session.page)
    assert session.retired
    assert session.state.status == "stopped"
    detected = session.state
    assert await browsers.stop("thread-test", session.state.session_id) == detected
    await session.close()
    assert session.context is session.page is None


async def test_unexpected_process_exit_fails_the_runtime(browsers):
    session, _ = await ready(browsers)
    process = Mock(wait=AsyncMock())
    session.watch_process(process, "The virtual display")
    await asyncio.gather(*tuple(session.monitors))
    assert session.state.status == "stopped"
    assert "virtual display" in session.state.error
    await session.close()


async def test_renderer_failure_is_a_safe_browser_error_and_retired_results_are_rejected(browsers):
    session, backend = await ready(browsers)
    session.page.screenshot.side_effect = RuntimeError("Renderer disappeared")
    with pytest.raises(HarnessUiError, match="outcome may be uncertain"):
        await backend.screenshot()

    async def closing(**kwargs):
        session.end("Chromium closed.")
        return b"stale screenshot"

    session.page.screenshot.side_effect = closing
    with pytest.raises(HarnessUiError, match="session changed"):
        await backend.screenshot()


async def test_reset_cleans_old_runtime_before_start_and_revokes_old_bindings(browsers, monkeypatch):
    session, backend = await ready(browsers)
    binding = session.state.session_id
    await browsers.control("thread-test", binding, human=True)
    pending = asyncio.create_task(backend.wait_for_handback())
    await asyncio.sleep(0)
    cleanup = AsyncMock()
    session.resources.push_async_callback(cleanup)
    session.profile.mkdir(parents=True)
    (session.profile / "saved-settings").write_text("retained")

    async def start(replacement):
        cleanup.assert_awaited_once()
        replacement.state = replacement.state.model_copy(update={"status": "ready"})

    monkeypatch.setattr(module.BrowserSession, "start", start)
    state = await browsers.reset("thread-test", binding)
    assert state.status == "ready"
    assert state.control == "agent"
    assert state.session_id != binding
    assert (session.profile / "saved-settings").read_text() == "retained"
    assert browsers.session("thread-test", state.session_id).requires_observation
    with pytest.raises(HarnessUiError, match="session changed"):
        await asyncio.wait_for(pending, 1)
    with pytest.raises(HarnessUiError, match="session changed"):
        await backend.screenshot()


async def test_stopped_sessions_release_capacity_but_start_still_enforces_the_limit(browsers):
    states = [await browsers.enable(f"thread-{i}") for i in range(4)]
    with pytest.raises(HarnessUiError, match="Stop an unused"):
        await browsers.enable("thread-extra")
    await browsers.stop("thread-0", states[0].session_id)
    await browsers.enable("thread-extra")
    with pytest.raises(HarnessUiError, match="Stop an unused"):
        await browsers.start("thread-0", states[0].session_id, explicit=True)
    assert browsers.state("thread-0").status == "stopped"


async def test_viewport_resize_requires_controller_and_invalidates_old_observation(browsers):
    session, _ = await ready(browsers)
    session.context = Mock(pages=[session.page])
    session.layout = AsyncMock()
    session.fit_display = AsyncMock()
    request = BrowserViewportRequest(session_id=session.state.session_id, preset="phone")
    with pytest.raises(HarnessUiError, match="Take control"):
        await browsers.resize("thread-test", request)
    control = await browsers.control("thread-test", session.state.session_id, human=True)
    with pytest.raises(HarnessUiError, match="Take control"):
        await browsers.resize("thread-test", request.model_copy(update={"control_token": "wrong"}))
    session.revision = "old-layout"
    state = await browsers.resize("thread-test", request.model_copy(update={"control_token": control.control_token}))
    assert state.viewport == "phone"
    assert state.session_id == request.session_id
    assert state.control == "human"
    assert session.requires_observation
    assert not session.revision
    session.layout.assert_awaited_once_with(session.page, "phone")


async def test_partial_viewport_failure_stops_the_runtime_instead_of_exposing_mismatched_input(browsers):
    session, backend = await ready(browsers)
    session.context = Mock(pages=[session.page])
    session.layout = AsyncMock(side_effect=RuntimeError("Display resize failed"))
    session.fit_display = AsyncMock()
    control = await browsers.control("thread-test", session.state.session_id, human=True)
    with pytest.raises(HarnessUiError, match="Browser resizing failed"):
        await browsers.resize(
            "thread-test",
            BrowserViewportRequest(
                session_id=session.state.session_id, control_token=control.control_token, preset="tablet"
            ),
        )
    assert session.state.status == "stopped"
    assert session.state.viewport == "desktop"
    assert not session.control_token
    with pytest.raises(HarnessUiError, match="session changed"):
        await backend.screenshot()
