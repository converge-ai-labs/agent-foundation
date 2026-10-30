"""Immediate input shares availability checks; ordinary inbox transactions own delivery."""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_service.infra.db import lock, now, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs import execute, inbox
from a13n_service.runs.mailbox import Mailbox
from a13n_service.runs.tables import RunRow, ThreadRow
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio


async def test_listeners_share_one_scan_and_last_listener_joins_it(monkeypatch):
    mailbox = Mailbox(None, interval=10)
    queries = asyncio.Queue()
    finish = asyncio.Event()

    async def pending(thread_ids):
        await queries.put(thread_ids)
        await finish.wait()
        return {"thread-4"}

    monkeypatch.setattr(mailbox, "_pending", pending)
    async with AsyncExitStack() as stack:
        listeners = [await stack.enter_async_context(mailbox.listen(f"thread-{i}")) for i in range(40)]
        duplicate = await stack.enter_async_context(mailbox.listen("thread-4"))
        task = mailbox._task
        batch = await asyncio.wait_for(queries.get(), 2)
        assert set(batch) == {f"thread-{i}" for i in range(40)}
        finish.set()
        await asyncio.wait_for(listeners[4].wait(), 2)
        assert duplicate.is_set()
        assert not any(ready.is_set() for i, ready in enumerate(listeners) if i != 4)
        assert queries.empty()
    assert task.done() and mailbox._task is None and not mailbox._listeners
    async with mailbox.listen("thread-new") as ready:
        assert mailbox._task is not task
        assert await asyncio.wait_for(queries.get(), 2) == ("thread-new",)
        assert not ready.is_set()
    assert mailbox._task is None


async def test_failed_scan_retries_without_losing_listeners(monkeypatch):
    mailbox = Mailbox(None, interval=0.01)
    attempts = 0

    async def pending(thread_ids):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("temporary database outage")
        return set(thread_ids)

    monkeypatch.setattr(mailbox, "_pending", pending)
    async with mailbox.listen("thread") as ready:
        await asyncio.wait_for(ready.wait(), 2)
        assert attempts >= 2
    assert mailbox._task is None


async def test_last_listener_cancels_inflight_scan(monkeypatch):
    mailbox = Mailbox(None, interval=10)
    entered, released = asyncio.Event(), asyncio.Event()

    async def pending(thread_ids):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    monkeypatch.setattr(mailbox, "_pending", pending)
    async with mailbox.listen("thread"):
        await asyncio.wait_for(entered.wait(), 2)
    assert released.is_set() and mailbox._task is None


@pytest.mark.parametrize("interruption", ["none", "model_backoff", "assignment_unavailable"])
async def test_receiver_keeps_listening_until_content_failure_ends_attempt(monkeypatch, interruption):
    mailbox = Mailbox(None, interval=0.01)
    boundaries = SimpleNamespace(accepting_steers=True)
    scans = 0

    async def pending(thread_ids):
        nonlocal scans
        scans += 1
        boundaries.accepting_steers = interruption != "model_backoff" or scans > 1
        return set(thread_ids)

    @asynccontextmanager
    async def transaction_scope(storage):
        yield None

    unavailable = ServiceError("unavailable", "Temporary assignment failure")
    assign = AsyncMock(side_effect=[unavailable, []] if interruption == "assignment_unavailable" else [[]])
    monkeypatch.setattr(mailbox, "_pending", pending)
    monkeypatch.setattr(execute, "transaction", transaction_scope)
    monkeypatch.setattr(execute, "lock_thread_lease", AsyncMock(return_value=(None, None, None, None)))
    monkeypatch.setattr(execute, "authorize_execution", AsyncMock())
    monkeypatch.setattr(inbox, "assign_steers", assign)
    content_error = ServiceError("unavailable", "Temporary input read failure", {"dependency": "url"})
    offer = AsyncMock(side_effect=content_error)
    attempt = SimpleNamespace(
        runtime=SimpleNamespace(
            mailbox=mailbox,
            storage=None,
            access=None,
            settings=SimpleNamespace(
                worker=SimpleNamespace(delivery_count=10, delivery_bytes=1000),
                control=SimpleNamespace(inbox_count=100),
            ),
        ),
        lease=SimpleNamespace(thread_id="thread"),
        boundaries=boundaries,
        _offer=offer,
    )
    with pytest.raises(ServiceError) as raised:
        await asyncio.wait_for(execute._Attempt._receive_input(attempt, None), 2)
    assert raised.value is content_error
    assert scans == (1 if interruption == "none" else 2)
    assert assign.await_count == (2 if interruption == "assignment_unavailable" else 1)
    offer.assert_awaited_once_with(None, [])
    assert mailbox._task is None and not mailbox._listeners


async def test_pending_scan_is_one_unlocked_query_and_only_hints_user_steers(
    service, scripted_model, runs_kit, monkeypatch
):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    threads = [await runs_kit.start_thread(service, agent, f"start {i}") for i in range(3)]
    ids = tuple(item["thread"]["id"] for item in threads)
    steer = await runs_kit.submit(service, ids[0], runs_kit.message(agent, "steer"))
    assert steer.status_code == 201, steer.text
    later = await runs_kit.submit(service, ids[1], runs_kit.message(agent, "later", delivery="next_run"))
    assert later.status_code == 201, later.text
    async with transaction(service.runtime.storage) as session:
        thread = await lock(session, ThreadRow, ids[2])
        origin = await session.get_one(RunRow, threads[2]["run"]["id"])
        await inbox.append_tool_result(session, thread, origin, {"execution_id": "exec-completed"})
    statements = []
    original = AsyncSession.scalars

    async def scalars(self, statement, *args, **kwargs):
        statements.append(str(statement))
        return await original(self, statement, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "scalars", scalars)
        assert await service.runtime.mailbox._pending(ids) == {ids[0]}
        assert len(statements) == 1
        assert "FOR UPDATE" not in statements[0]
        assert "SELECT DISTINCT inbox_entries.thread_id" in statements[0]
    # Availability checks never assign or consume input; a withdrawn hint is harmless.
    entry_id = steer.json()["entry"]["id"]
    entries = {entry["id"]: entry for entry in await runs_kit.inbox(service, ids[0])}
    assert entries[entry_id]["status"] == "pending"
    async with transaction(service.runtime.storage) as session:
        thread = await lock(session, ThreadRow, ids[0])
        entry = await inbox.get_entry(session, thread, entry_id, lock=True)
        entry.status, entry.finished_at = "withdrawn", await now(session)
    assert await service.runtime.mailbox._pending(ids) == set()


async def test_local_notification_waits_for_commit_and_ignores_rollback_and_system_input(service, monkeypatch):
    mailbox = service.runtime.mailbox

    async def no_remote_input(thread_ids):
        return set()

    monkeypatch.setattr(mailbox, "_pending", no_remote_input)
    entry = SimpleNamespace(thread_id="thread", kind="message", delivery="steer")
    async with mailbox.listen("thread") as ready:
        async with transaction(service.runtime.storage) as session:
            mailbox.notify(session, entry)
            assert not ready.is_set()
        assert ready.is_set()
        ready.clear()
        with pytest.raises(ValueError, match="rollback"):
            async with transaction(service.runtime.storage) as session:
                mailbox.notify(session, entry)
                raise ValueError("rollback")
        assert not ready.is_set()
        for kind, delivery in [("message", "next_run"), ("child_result", "steer"), ("tool_result", "steer")]:
            async with transaction(service.runtime.storage) as session:
                mailbox.notify(session, SimpleNamespace(thread_id="thread", kind=kind, delivery=delivery))
            assert not ready.is_set()
