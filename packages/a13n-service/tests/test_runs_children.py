"""Async child runs: their results reaching the parent thread, and the parent steering them.

The control sweeps are paused, so each test runs attempts and outbox deliveries itself, in order.
"""

import asyncio
import json
from collections.abc import Iterable
from datetime import timedelta

import pytest
from a13n_service.infra import outbox
from a13n_service.infra.db import transaction
from a13n_service.infra.outbox import OutboxRow
from a13n_service.runs import accept as accept_module
from a13n_service.runs.attempts import AttemptControl
from a13n_service.runs.children import FULL_INBOX_SECONDS, child_results
from a13n_service.runs.claim import claim
from a13n_service.runs.execute import execute
from a13n_service.runs.subagents import ChildRuns
from a13n_service.runs.tables import RunRow, ThreadRow
from sqlalchemy import func, select, update

pytestmark = pytest.mark.anyio

DELEGATE = {"subagent_name": "helper", "prompt": "compute"}


async def _deliver(service) -> list[outbox.Claim]:  # type: ignore[no-untyped-def]
    """Claim the due child results and deliver them, as one pass of the outbox sweep does."""
    claims = await outbox.claim(
        service.runtime.storage, "child_result", owner="test", limit=10, lease_seconds=60, max_attempts=12
    )
    for claimed in claims:
        await child_results(service.runtime)(claimed)
    return claims


def _tool_results(model) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """What each tool call returned to the model, by call ID, from the requests received so far."""
    requests = [model.requests.get_nowait() for _ in range(model.requests.qsize())]
    return {
        message["tool_call_id"]: message["content"]
        for request in requests
        for message in request["messages"]
        if message["role"] == "tool"
    }


async def _child_run(service, parent_run_id: str) -> RunRow:  # type: ignore[no-untyped-def]
    """The active run of the one child thread the parent run spawned."""
    async with transaction(service.runtime.storage) as session:
        child = (await session.scalars(select(ThreadRow).where(ThreadRow.origin_run_id == parent_run_id))).one()
        return await session.get_one(RunRow, child.current_run_id)


async def test_a_question_response_keeps_queued_child_results(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    coordinator = await runs_kit.delegating(service, scripted_model, "async", user_questions=True)
    scripted_model.call("delegate", DELEGATE, call_id="call_d", to="Role: coordinator")
    scripted_model.call(
        "ask_user_question", {"questions": [runs_kit.QUESTION]}, call_id="call_ask", to="Role: coordinator"
    )
    submitted = await runs_kit.start_thread(service, coordinator, "delegate, then ask me")
    thread_id = submitted["thread"]["id"]
    await (await runs_kit.attempt(service))
    waiting = await runs_kit.get_run(service, submitted["run"]["id"])
    assert waiting["wait_reason"] == "call", waiting
    scripted_model.say("42", to="Role: worker")
    await (await runs_kit.attempt(service))
    await _deliver(service)
    # A child result cannot resolve a question; it remains queued for explicit continuation.
    assert [(entry["kind"], entry["status"]) for entry in await runs_kit.inbox(service, thread_id)] == [
        ("message", "consumed"),
        ("child_result", "pending"),
    ]

    await accept_module.ThreadAdvancer(service.runtime, batch=1)()
    assert (await runs_kit.get_thread(service, thread_id))["current_run_id"] is None
    reply = await service.client.post(
        f"{service.api}/runs/{waiting['id']}/resume",
        json={"approvals": {}, "calls": {"call_ask": {"status": "returned", "value": {"response": "blue"}}}},
        headers=runs_kit.fresh_key(),
    )
    assert reply.status_code == 201, reply.text
    run = reply.json()
    assert (run["trigger"], run["parent_run_id"]) == ("resume", waiting["id"])
    scripted_model.say("Answer received", to="Role: coordinator")
    # The child steer can join this successor after its first request or start another Run.
    # Script both replies before execution: waiting until it seals deadlocks the first case.
    scripted_model.say("Child result received", to="Role: coordinator")
    await (await runs_kit.attempt(service))
    child_result = (await runs_kit.inbox(service, thread_id))[1]
    assert child_result["status"] in {"assigned", "consumed"}
    if child_result["status"] == "assigned":
        await (await runs_kit.attempt(service))
    assert [entry["status"] for entry in await runs_kit.inbox(service, thread_id)] == ["consumed", "consumed"]


async def test_a_child_result_waits_for_room_and_is_delivered_once(serve, settings, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    tight = settings.model_copy(update={"control": settings.control.model_copy(update={"inbox_count": 1})})
    async with serve(settings=tight) as service:
        await runs_kit.pause_sweeps(service)
        coordinator = await runs_kit.delegating(service, scripted_model, "async")
        scripted_model.call("delegate", DELEGATE, call_id="call_d", to="Role: coordinator")
        scripted_model.say("Delegated", to="Role: coordinator")
        thread_id = (await runs_kit.start_thread(service, coordinator, "delegate"))["thread"]["id"]
        await (await runs_kit.attempt(service))
        scripted_model.say("42", to="Role: worker")
        await (await runs_kit.attempt(service))

        # The parent's next run holds its source entry, so the inbox has no room for the result.
        busy = await runs_kit.submit(service, thread_id, runs_kit.message(coordinator, "next"))
        assert busy.status_code == 201, busy.text
        assert len(await _deliver(service)) == 1
        async with transaction(service.runtime.storage) as session:
            row = (await session.scalars(select(OutboxRow).where(OutboxRow.kind == "child_result"))).one()
            assert (row.status, row.last_error, row.attempts) == ("pending", "inbox_full", 0)
            # A full inbox is looked at again after a fixed delay, not at the retry backoff's first steps.
            deferred = await session.scalar(select(OutboxRow.available_at - func.now()).where(OutboxRow.id == row.id))
            assert deferred is not None and deferred > timedelta(seconds=FULL_INBOX_SECONDS - 5)
            await session.execute(update(OutboxRow).where(OutboxRow.id == row.id).values(available_at=func.now()))
        assert [entry["kind"] for entry in await runs_kit.inbox(service, thread_id)] == ["message", "message"]
        interrupted = await service.client.post(f"{service.api}/runs/{busy.json()['run']['id']}/interrupt")
        assert interrupted.status_code == 200, interrupted.text

        # A sender whose lease ran out and was claimed again delivers nothing; the new claim delivers it once.
        storage, deliver = service.runtime.storage, child_results(service.runtime)
        (stale,) = await outbox.claim(storage, "child_result", owner="stale", limit=1, lease_seconds=0, max_attempts=12)
        (current,) = await outbox.claim(
            storage, "child_result", owner="current", limit=1, lease_seconds=60, max_attempts=12
        )
        await deliver(stale)
        await deliver(current)
        results = [entry for entry in await runs_kit.inbox(service, thread_id) if entry["kind"] == "child_result"]
        assert [entry["status"] for entry in results] == ["pending"]
        async with transaction(service.runtime.storage) as session:
            assert (await session.get_one(OutboxRow, row.id)).status == "delivered"


async def test_a_parent_steers_its_usage_limited_child(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    edge = {"usage_limits": {"request_limit": 5}}
    worker = {"toolsets": {"configuration": {"enabled": True}}}
    coordinator = await runs_kit.delegating(service, scripted_model, "async", edge=edge, worker=worker)
    scripted_model.call("delegate", DELEGATE, call_id="call_d", to="Role: coordinator")
    scripted_model.say("Delegated", to="Role: coordinator")
    submitted = await runs_kit.start_thread(service, coordinator, "delegate")
    thread_id = submitted["thread"]["id"]
    await (await runs_kit.attempt(service))
    child = await _child_run(service, submitted["run"]["id"])
    child_run_id = child.id
    assert child.options["max_usage"] == {"requests": 5}

    # The parent's next run steers the child before the child starts.
    async with transaction(service.runtime.storage) as session:
        held = update(RunRow).where(RunRow.id == child_run_id)
        await session.execute(held.values(available_at=func.now() + timedelta(hours=1)))
    steer = {"execution_id": child_run_id, "message": "report it in metres"}
    scripted_model.call("steer_subagent", steer, call_id="call_s", to="Role: coordinator")
    scripted_model.say("Steered", to="Role: coordinator")
    assert (await runs_kit.submit(service, thread_id, runs_kit.message(coordinator, "steer it"))).status_code == 201
    await (await runs_kit.attempt(service))
    async with transaction(service.runtime.storage) as session:
        await session.execute(held.values(available_at=RunRow.created_at))

    # Steers join at the child's next boundary: after its tool call it reads the steer, or asks once more for it.
    scripted_model.call("find_resources", {"kind": "model"}, call_id="call_find", to="Role: worker")
    scripted_model.say("42", to="Role: worker")
    scripted_model.say("42 metres", to="Role: worker")
    await (await runs_kit.attempt(service))
    (steered,) = [entry for entry in await runs_kit.inbox(service, child.thread_id) if entry["delivery"] == "steer"]
    assert (steered["status"], steered["assigned_run_id"]) == ("consumed", child_run_id), steered


async def test_a_refused_subagent_call_fails_only_that_tool_call(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    coordinator = await runs_kit.delegating(service, scripted_model, "async")
    steer = {"execution_id": "run_missing", "message": "hi"}
    scripted_model.call("steer_subagent", steer, call_id="call_s", to="Role: coordinator")
    scripted_model.say("Recovered", to="Role: coordinator")
    submitted = await runs_kit.start_thread(service, coordinator, "steer a missing child")
    await (await runs_kit.attempt(service))

    run = await runs_kit.get_run(service, submitted["run"]["id"])
    assert (run["status"], run["output"]) == ("completed", "Recovered"), run
    assert "execution run_missing not found" in _tool_results(scripted_model)["call_s"]


async def test_a_parent_reads_its_children_a_page_at_a_time(service, scripted_model, runs_kit, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    coordinator = await runs_kit.delegating(service, scripted_model, "async")
    for call_id in ("call_d1", "call_d2"):
        scripted_model.call("delegate", DELEGATE, call_id=call_id, to="Role: coordinator")
    scripted_model.say("Delegated", to="Role: coordinator")
    submitted = await runs_kit.start_thread(service, coordinator, "delegate twice")
    thread_id = submitted["thread"]["id"]
    await (await runs_kit.attempt(service))
    async with transaction(service.runtime.storage) as session:
        children = (
            await session.scalars(
                select(ThreadRow)
                .where(ThreadRow.origin_run_id == submitted["run"]["id"])
                .order_by(ThreadRow.created_at)
            )
        ).all()
        first, second = [child.current_run_id for child in children]
        await session.execute(
            update(RunRow).where(RunRow.id == second).values(available_at=func.now() + timedelta(hours=1))
        )
    scripted_model.say("one", to="Role: worker")
    await (await runs_kit.attempt(service))

    # The parent's next run reads the first page, then waits on the second one's still running child.
    scripted_model.call("subagent_info", {"execution_limit": 1}, call_id="call_info", to="Role: coordinator")
    wait = {"execution_offset": 1, "execution_limit": 1, "timeout_seconds": 0.5}
    scripted_model.call("wait_subagent", wait, call_id="call_wait", to="Role: coordinator")
    scripted_model.say("Checked", to="Role: coordinator")
    reads: list[str] = []
    page, statuses = ChildRuns._page, ChildRuns._statuses

    async def paged(operator: ChildRuns, execution_id: str | None, offset: int, limit: int) -> tuple[list, int]:
        reads.append("page")
        return await page(operator, execution_id, offset, limit)

    async def polled(operator: ChildRuns, run_ids: Iterable[str]) -> dict[str, str]:
        reads.append("statuses")
        return await statuses(operator, run_ids)

    monkeypatch.setattr(ChildRuns, "_page", paged)
    monkeypatch.setattr(ChildRuns, "_statuses", polled)
    assert (await runs_kit.submit(service, thread_id, runs_kit.message(coordinator, "check"))).status_code == 201
    await (await runs_kit.attempt(service))

    results = _tool_results(scripted_model)
    info, waited = json.loads(results["call_info"]), json.loads(results["call_wait"])
    assert (info["total"], info["next_offset"]) == (2, 1)
    assert [(item["execution_id"], item["status"], json.loads(item["input"])) for item in info["executions"]] == [
        (first, "succeeded", {"delegated_task": "compute"})
    ]
    assert (waited["total"], waited["next_offset"]) == (2, None)
    assert [(item["execution_id"], item["status"]) for item in waited["executions"]] == [(second, "running")]
    # The info call and the wait each read their page once; the wait then polls only its runs' statuses.
    assert reads[:2] == ["page", "page"] and set(reads[2:]) == {"statuses"}, reads


async def test_a_wait_for_children_ends_when_the_worker_drains(service, scripted_model, runs_kit, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A draining worker's attempt yields at its next boundary instead of outwaiting the drain on a child."""
    await runs_kit.pause_sweeps(service)
    coordinator = await runs_kit.delegating(service, scripted_model, "async")
    scripted_model.call("delegate", DELEGATE, call_id="call_d", to="Role: coordinator")
    scripted_model.call("wait_subagent", {"timeout_seconds": 300}, call_id="call_w", to="Role: coordinator")
    run_id = (await runs_kit.start_thread(service, coordinator, "delegate and wait"))["run"]["id"]
    polling = asyncio.Event()
    statuses = ChildRuns._statuses

    async def polled(operator: ChildRuns, run_ids: Iterable[str]) -> dict[str, str]:
        polling.set()
        return await statuses(operator, run_ids)

    monkeypatch.setattr(ChildRuns, "_statuses", polled)
    (lease,) = await claim(service.runtime, worker_id="worker-test", worker_build="test", limit=1)
    control = AttemptControl(deadline=asyncio.get_running_loop().time() + service.runtime.settings.worker.lease_seconds)
    running = asyncio.create_task(execute(service.runtime, lease, control))
    async with asyncio.timeout(10):
        await polling.wait()
    # The request after the wait is cancelled as it starts; whether it reached the model does not matter.
    gate = asyncio.Event()
    scripted_model.say("Waited", gate=gate, to="Role: coordinator")
    control.handoff.set()
    async with asyncio.timeout(10):
        await running
    gate.set()
    scripted_model.turns.clear()

    assert (await runs_kit.get_run(service, run_id))["status"] == "accepted"
    attempts = (await service.client.get(f"{service.api}/runs/{run_id}/attempts")).json()["items"]
    assert [item["status"] for item in attempts] == ["yielded"]


async def test_inline_children_share_display_producer_but_keep_scoped_blocks(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    coordinator = await runs_kit.delegating(service, scripted_model, "inline")
    scripted_model.call(
        "delegate", {"subagent": "helper", "prompt": "compute"}, call_id="call_d", to="Role: coordinator"
    )
    scripted_model.say("Child answer", to="Role: worker")
    scripted_model.say("Root answer", to="Role: coordinator")
    submitted = await runs_kit.start_thread(service, coordinator, "delegate")
    run_id = submitted["run"]["id"]
    await (await runs_kit.attempt(service))
    listing = await runs_kit.items(service, run_id)
    assert listing["run"]["status"] == "completed", listing
    snapshot = listing["snapshot"]
    assert snapshot["position"]["producer"] == {"run_id": run_id, "generation": "1"}
    roots = [scope for scope in snapshot["scopes"] if scope["parent_scope_id"] is None]
    children = [scope for scope in snapshot["scopes"] if scope["parent_scope_id"] is not None]
    assert len(roots) == len(children) == 1
    root, child = roots[0], children[0]
    assert child["parent_scope_id"] == root["id"] and child["parent_tool_call_id"] == "call_d"
    assert root["status"] == child["status"] == "completed"
    assert [
        (block["scope_id"], block["content"]["text"]) for block in snapshot["blocks"] if block["kind"] == "text"
    ] == [
        (child["id"], "Child answer"),
        (root["id"], "Root answer"),
    ]
    calls = [block for block in snapshot["blocks"] if block["kind"] == "tool_chunk"]
    assert len(calls) == 1 and calls[0]["status"] == "succeeded"
    summaries = [block for block in snapshot["blocks"] if block["content"].get("name") == "a13n.display.model_request"]
    assert len(summaries) == 3 and all(block["status"] == "succeeded" for block in summaries)
