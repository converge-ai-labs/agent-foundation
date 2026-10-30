"""Browser bookkeeping across control changes, target loss and concurrent Thread writes."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from a13n_harness.capabilities.browser import BrowserError, BrowserHumanControl
from a13n_service.infra import outbox
from a13n_service.infra.db import short_session, transaction
from a13n_service.runs.attempts import AttemptControl
from a13n_service.runs.browsers import admission, control, execution, lifecycle
from a13n_service.runs.browsers import service as browsers
from a13n_service.runs.browsers.delivery import handbacks
from a13n_service.runs.browsers.tables import BrowserBlockedRow, BrowserSelectionRow, BrowserTabRow
from a13n_service.runs.claim import claim
from a13n_service.runs.execute import execute
from sqlalchemy import select, text

from .test_browsers import bound_run, command, setup
from .test_browsers import invocation as invocation

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("cause", ["rebind", "stop", "expiry", "valid"])
async def test_new_block_replaces_only_suppressed_registration(service, runs_kit, scripted_model, cause):
    _, browser = await setup(service, runs_kit)
    agent = await runs_kit.create_agent(service, scripted_model)
    first, binding = await bound_run(service, runs_kit, scripted_model, browser, agent=agent)
    await command(service, runs_kit, browser["id"], "takeover", thread_id=first.thread_id)
    with pytest.raises(BrowserHumanControl):
        await admission.admit(service.runtime, first, binding)
    async with transaction(service.runtime.storage) as session:
        old_id = (await session.scalars(select(BrowserBlockedRow))).one().id
    attempt = AttemptControl(deadline=asyncio.get_running_loop().time() + 60)
    if cause == "stop":
        response = await service.client.post(f"{service.api}/runs/{first.run_id}/interrupt")
        assert response.status_code == 200, response.text
        attempt.stopped.set()  # Deliver the supervisor's cancellation signal to this directly driven attempt.
    else:
        scripted_model.say("Unrelated work complete")
    await execute(service.runtime, first, attempt)
    assert (await runs_kit.get_run(service, first.run_id))["status"] == (
        "cancelled" if cause == "stop" else "completed"
    )
    if cause == "rebind":
        thread = await runs_kit.get_thread(service, first.thread_id)
        binding = binding.model_copy(update={"tab_access": "global"})
        response = await service.client.put(
            f"{service.api}/threads/{first.thread_id}/browser",
            json=binding.model_dump(mode="json"),
            headers=runs_kit.if_match(thread),
        )
        assert response.status_code == 200, response.text
    elif cause == "expiry":
        async with transaction(service.runtime.storage) as session:
            (await session.get_one(BrowserBlockedRow, old_id)).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    response = await runs_kit.submit(service, first.thread_id, runs_kit.message(agent, "New browser task"))
    assert response.status_code == 201, response.text
    (second,) = await claim(service.runtime, worker_id="second", worker_build="test", limit=1)
    async with transaction(service.runtime.storage) as session:
        # Ordinary input and acceptance alone must not renew an old obligation.
        old = await session.get_one(BrowserBlockedRow, old_id)
        assert old.run_id == first.run_id
        assert old.suppressed == (cause in {"rebind", "stop"})
    for _ in range(2):
        with pytest.raises(BrowserHumanControl, match="notice is registered"):
            await admission.admit(service.runtime, second, binding)
    async with transaction(service.runtime.storage) as session:
        registration = (await session.scalars(select(BrowserBlockedRow))).one()
        assert not registration.suppressed
        # Active obligations follow the episode, even if a pre-upgrade finite TTL elapsed.
        assert registration.expires_at is None or cause == "expiry"
        original = cause in {"valid", "expiry"}
        assert registration.run_id == (first.run_id if original else second.run_id)
        assert (registration.id == old_id) == original
        registration_id = registration.id
    await command(service, runs_kit, browser["id"], "handback")
    (notice,) = await outbox.claim(
        service.runtime.storage, "browser_handback", owner="test", limit=10, lease_seconds=60, max_attempts=12
    )
    assert notice.payload["registration_id"] == registration_id
    await handbacks(service.runtime)(notice)
    entries = [entry for entry in await runs_kit.inbox(service, first.thread_id) if entry["kind"] == "browser_handback"]
    assert len(entries) == 1


@pytest.mark.parametrize("tab_access", ["thread", "global"])
@pytest.mark.parametrize("close_kind", ["explicit", "external_during_list", "event"])
async def test_target_loss_clears_durable_selection(
    service, runs_kit, scripted_model, invocation, monkeypatch, tab_access, close_kind
):
    supplier, browser = await setup(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser, tab_access=tab_access)
    async with execution.open_backend(
        service.runtime, lease, binding, invocation, frozenset({"tabs", "dialog"})
    ) as backend:
        await backend.tabs("new")
        supplier.pages.add("popup")
        supplier.openers["popup"] = "new-1"
        await backend.tabs("select", target="popup")
        if close_kind == "explicit":
            tabs = await backend.tabs("close", target="popup")
        elif close_kind == "event":
            supplier.pages.remove("popup")
            backend.native.browser._destroyed({"targetId": "popup"}, None)
            tabs = await backend.tabs("list")
        else:
            original = backend.native.tabs

            async def disappear(*args, **kwargs):
                supplier.pages.discard("popup")
                return await original(*args, **kwargs)

            monkeypatch.setattr(backend.native, "tabs", disappear)
            tabs = await backend.tabs("list")
        assert "new-1" in {tab.target for tab in tabs}
        assert not any(tab.selected for tab in tabs)
        async with transaction(service.runtime.storage) as session:
            assert (await session.get_one(BrowserSelectionRow, (browser["id"], lease.thread_id))).target_id is None
        with pytest.raises(BrowserError, match="No selected"):
            await backend.dialog("inspect")
        await backend.tabs("select", target="new-1")
        await backend.dialog("inspect")
        async with transaction(service.runtime.storage) as session:
            assert (await session.get_one(BrowserSelectionRow, (browser["id"], lease.thread_id))).target_id == "new-1"


async def wait_for_blocked_transaction(storage, blocker):
    """Observe the real PostgreSQL wait graph, not a timing assumption about Python tasks."""
    async with asyncio.timeout(5):
        while True:
            async with short_session(storage) as session:
                blocked = await session.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE :pid = ANY(pg_blocking_pids(pid)))"),
                    {"pid": blocker},
                )
            if blocked:
                return
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("phase", ["save", "popup_sync", "handback"])
async def test_ownership_write_and_rebind_keep_thread_first_lock_order(
    service, runs_kit, scripted_model, invocation, monkeypatch, phase
):
    supplier, browser = await setup(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    thread_locked = asyncio.Event()
    write_entered = asyncio.Event()
    original_thread = browsers.get_thread

    async def get_thread(session, workspace_id, thread_id, *, lock=False, **kwargs):
        row = await original_thread(session, workspace_id, thread_id, lock=lock, **kwargs)
        if asyncio.current_task().get_name() == "rebind" and lock:
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            thread_locked.set()
            await wait_for_blocked_transaction(service.runtime.storage, pid)
        return row

    monkeypatch.setattr(browsers, "get_thread", get_thread)
    async with execution.open_backend(service.runtime, lease, binding, invocation, frozenset({"tabs"})) as backend:
        if phase != "save":
            await backend.tabs("new")
        if phase == "handback":
            await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
            supplier.pages.add("human-tab")
            original_inventory = control.inventory

            async def inventory(*args):
                result = await original_inventory(*args)
                write_entered.set()
                await thread_locked.wait()
                return result

            monkeypatch.setattr(control, "inventory", inventory)
            call = asyncio.create_task(command(service, runs_kit, browser["id"], "handback"))
        else:
            method = "_save" if phase == "save" else "_sync"
            original = getattr(execution.Backend, method)

            async def write(self, native, permit):
                write_entered.set()
                await thread_locked.wait()
                return await original(self, native, permit)

            monkeypatch.setattr(execution.Backend, method, write)
            if phase == "popup_sync":
                supplier.pages.add("popup")
                supplier.openers["popup"] = "new-1"
            call = asyncio.create_task(backend.tabs("new" if phase == "save" else "list"))
        await asyncio.wait_for(write_entered.wait(), 5)
        thread = await runs_kit.get_thread(service, lease.thread_id)
        rebind = asyncio.create_task(
            service.client.put(
                f"{service.api}/threads/{lease.thread_id}/browser",
                json=binding.model_dump(mode="json"),
                headers=runs_kit.if_match(thread),
            ),
            name="rebind",
        )
        results = await asyncio.wait_for(asyncio.gather(call, rebind, return_exceptions=True), 15)
    assert not any(isinstance(result, BaseException) for result in results), [str(result) for result in results]
    assert results[1].status_code == 200, results[1].text
    async with transaction(service.runtime.storage) as session:
        assert not await lifecycle.unsettled(session, browser["id"])
        owners = {row.target_id: row.thread_id for row in await session.scalars(select(BrowserTabRow))}
        assert owners["new-1"] == lease.thread_id
        if phase != "save":
            assert owners["human-tab" if phase == "handback" else "popup"] == lease.thread_id
    current = await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    assert current["control"] == "human" and len(supplier.grants) == 1
