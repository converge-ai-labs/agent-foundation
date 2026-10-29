"""Opt-in existing Chromium -> attach -> bound native tools, with disposable stores/profile."""

import asyncio
from dataclasses import replace

import pytest
from a13n_harness.capabilities.browser import BrowserError
from a13n_harness.capabilities.browser.cdp import Chromium
from a13n_service.infra.db import transaction
from a13n_service.runs.attempts import AttemptControl
from a13n_service.runs.browsers import execution, lifecycle
from a13n_service.runs.browsers.tables import BrowserSelectionRow, BrowserTabRow
from a13n_service.runs.claim import claim
from a13n_service.runs.execute import execute
from sqlalchemy import select

from .test_browser_external import attach
from .test_browsers import bound_run, command
from .test_browsers import invocation as invocation

pytestmark = pytest.mark.anyio


@pytest.fixture
async def external_chromium(request, browser_executable, tmp_path):
    arguments = [
        browser_executable,
        f"--user-data-dir={tmp_path / 'profile'}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=0",
        "--headless=new",
        "--password-store=basic",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]
    if request.config.getoption("--browser-no-sandbox"):
        arguments += ["--no-sandbox", "--disable-dev-shm-usage"]
    process = await asyncio.create_subprocess_exec(*arguments, stdout=asyncio.subprocess.DEVNULL)
    try:
        active = tmp_path / "profile" / "DevToolsActivePort"
        async with asyncio.timeout(20):
            while True:
                assert process.returncode is None, "Chromium exited before CDP startup"
                if active.exists() and len(parts := active.read_text().splitlines()) == 2:
                    break
                await asyncio.sleep(0.05)
        yield f"ws://127.0.0.1:{parts[0]}{parts[1]}", process
    finally:
        # Only this fixture owns process termination, after attachment preservation assertions.
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()


async def finish(service, runs_kit, scripted_model, lease):
    scripted_model.say("Done")
    await execute(service.runtime, lease, AttemptControl(deadline=asyncio.get_running_loop().time() + 60))
    run = await runs_kit.get_run(service, lease.run_id)
    assert run["status"] == "completed", run.get("failure")


async def test_external_attach_native_tools_and_cross_run_reconnect(
    service, runs_kit, scripted_model, invocation, external_chromium
):
    endpoint, process = external_chromium
    origin = endpoint.replace("ws://", "http://").split("/devtools/")[0]
    providers = service.runtime.settings.providers.model_copy(update={"http_origins": (origin,)})
    service.runtime = replace(
        service.runtime, settings=service.runtime.settings.model_copy(update={"providers": providers})
    )
    service.app.state.runtime = service.runtime
    observer = await Chromium.connect(endpoint, discover=False)
    try:
        original = {
            info["targetId"]
            for info in (await observer.send("Target.getTargets"))["targetInfos"]
            if info["type"] == "page"
        }
        browser = await attach(service, runs_kit, cdp_url=endpoint, headers={})
        agent = await runs_kit.create_agent(service, scripted_model, toolsets={"browser": {"enabled": True}})
        first, binding = await bound_run(service, runs_kit, scripted_model, browser, agent=agent)
        scripted_model.call("browser_tabs", {"action": "new"}, call_id="call_external_new")
        scripted_model.call("browser_dialog", {"action": "inspect"}, call_id="call_external_dialog")
        await finish(service, runs_kit, scripted_model, first)
        assert process.returncode is None
        async with transaction(service.runtime.storage) as session:
            ownership = (await session.scalars(select(BrowserTabRow))).one()
            owned = ownership.target_id
            assert owned not in original and ownership.thread_id == first.thread_id
            assert (await session.get_one(BrowserSelectionRow, (browser["id"], first.thread_id))).target_id == owned
            assert not await lifecycle.unsettled(session, browser["id"])
        # A new Run borrows the same attachment with exact persisted ownership and selection.
        response = await runs_kit.submit(service, first.thread_id, runs_kit.message(agent, "Continue"))
        assert response.status_code == 201, response.text
        (second,) = await claim(service.runtime, worker_id="external-worker", worker_build="test", limit=1)
        async with execution.open_backend(
            service.runtime, second, binding, invocation, frozenset({"tabs", "dialog"})
        ) as backend:
            tabs = await backend.tabs("list")
            assert {tab.target for tab in tabs} == {owned} and next(tab for tab in tabs if tab.selected).target == owned
            await backend.native.browser.send(
                "Runtime.evaluate",
                {"expression": "window.open('about:blank')", "userGesture": True},
                backend.native.page,
            )
            async with asyncio.timeout(5):
                while len(tabs := await backend.tabs("list")) < 2:
                    await asyncio.sleep(0.01)
            popup = ({tab.target for tab in tabs} - {owned}).pop()
            await backend.tabs("select", target=popup)
            old = backend.native.browser
            await old.client.ws.close()
            tabs = await backend.tabs("list")
            assert backend.native.browser is not old
            assert next(tab for tab in tabs if tab.selected).target == popup
            await observer.send("Target.closeTarget", {"targetId": popup})
            tabs = await backend.tabs("list")
            assert {tab.target for tab in tabs} == {owned} and not any(tab.selected for tab in tabs)
            with pytest.raises(BrowserError, match="No selected"):
                await backend.dialog("inspect")
        await finish(service, runs_kit, scripted_model, second)
        # Global access sees the original unowned page without acquiring it.
        global_run, global_binding = await bound_run(
            service, runs_kit, scripted_model, browser, agent=agent, tab_access="global"
        )
        async with execution.open_backend(
            service.runtime, global_run, global_binding, invocation, frozenset({"tabs"})
        ) as backend:
            tabs = await backend.tabs("list")
            assert {tab.target for tab in tabs} == original | {owned}
            await backend.tabs("select", target=next(iter(original)))
        async with transaction(service.runtime.storage) as session:
            assert {row.target_id for row in await session.scalars(select(BrowserTabRow))} == {owned, popup}
        await finish(service, runs_kit, scripted_model, global_run)
        thread = await runs_kit.get_thread(service, global_run.thread_id)
        detached = await service.client.put(
            f"{service.api}/threads/{global_run.thread_id}/browser",
            content="null",
            headers={**runs_kit.if_match(thread), "content-type": "application/json"},
        )
        assert detached.status_code == 200 and detached.json() is None, detached.text
        current = await command(service, runs_kit, browser["id"], "release")
        assert current["status"] == "deleted" and process.returncode is None
        assert {
            info["targetId"]
            for info in (await observer.send("Target.getTargets"))["targetInfos"]
            if info["type"] == "page"
        } == original | {owned}
    finally:
        await observer.close()
