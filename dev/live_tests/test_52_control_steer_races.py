"""Steer admission versus waiting advancement, terminal sealing, and consumption."""

import asyncio
from uuid import uuid4

import pytest

from .client import agent_input
from .control_support import assert_absent, assert_tokens_once

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("operation", ["feedback", "waiting_continue"])
@pytest.mark.parametrize("winner", ["steer", "successor"])
async def test_waiting_steer_and_successor_commit_order_controls_binding(control, operation, winner):
    journey, live = control, control.live
    case, waiting = await journey.waiting()
    thread = await live.thread(waiting["thread_id"])
    path, body = await journey.command(waiting, operation)
    advancement = journey.arm("successor", "control.state_published", role="control", parent_run_id=waiting["id"])
    admission = journey.arm("steer", "control.steer_prepared", role="control", run_id=waiting["id"])
    first = journey.arm("first-response", "model.request", role="control", case_id=case["case_id"], request=2)
    token, key = uuid4().hex, uuid4().hex
    steer_path = f"/api/v1/runs/{waiting['id']}/steer"
    steer_body = agent_input("LIVE_STEER " + token)
    tasks = []
    try:
        advancing = asyncio.create_task(live.http.post(path, json=body, headers={"Idempotency-Key": uuid4().hex}))
        tasks.append(advancing)
        await journey.reached(advancement)
        steering = asyncio.create_task(live.http.post(steer_path, json=steer_body, headers={"Idempotency-Key": key}))
        tasks.append(steering)
        await journey.reached(admission)
        assert not advancing.done() and not steering.done()
        assert await live.thread(thread["id"]) == thread
        if winner == "steer":
            journey.release(admission)
            reply = await steering
            assert reply.status_code == 202, reply.text
            steer = reply.json()
            rows = await journey.inbox(thread["id"])
            assert len(rows) == 1 and rows[0]["id"] == steer["steer_id"]
            assert rows[0]["target_run_id"] is None and rows[0]["source_waiting_run_id"] == waiting["id"]
            assert await live.thread(thread["id"]) == thread
        journey.release(advancement)
        reply = await advancing
        assert reply.status_code == 202, reply.text
        successor = reply.json()
        successor = successor.get("run", successor)
        live.track(successor)
        await journey.reached(first)
        if winner == "successor":
            journey.release(admission)
            reply = await steering
            assert reply.status_code == 409, reply.text
            assert await journey.inbox(thread["id"]) == []
        else:
            rows = await journey.inbox(thread["id"])
            assert rows[0]["status"] == "pending" and rows[0]["target_run_id"] == successor["run_id"]
            assert rows[0]["source_waiting_run_id"] == waiting["id"]
            assert await journey.post(steer_path, steer_body, key=key, expected=202) == steer
        assert_absent(journey.observations(case), [token])
        assert (await live.thread(thread["id"]))["version"] == thread["version"] + 1
        journey.release(first)
        completed = await live.finish(successor["run_id"])
        if winner == "steer":
            assert completed["output_text"] == "STEERS:" + token
            assert_tokens_once(journey.observations(case)[-1], [token])
            rows = await journey.inbox(thread["id"])
            assert rows[0]["status"] == "consumed" and rows[0]["consumed_by_run_id"] == successor["run_id"]
        else:
            assert_absent(journey.observations(case), [token])
        assert await live.run(waiting["id"]) == waiting
        assert len(await journey.thread_runs(waiting)) == 2
    finally:
        for barrier in (advancement, admission, first):
            journey.release(barrier)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("outcome", ["cancelled", "failed"])
@pytest.mark.parametrize("winner", ["steer", "terminal"])
async def test_steer_admission_and_terminal_seal_preserve_the_winning_order(control, outcome, winner):
    journey, live = control, control.live
    case = (
        await journey.case(effect=True) if outcome == "cancelled" else await journey.case(failure="401", failures=100)
    )
    terminal = (
        journey.arm("terminal", "tool.before_effect", case_id=case["case_id"])
        if outcome == "cancelled"
        else journey.arm("terminal", "control.failure_prepared", retryable=False)
    )
    source = await journey.start(case)
    await journey.reached(terminal)
    assert (await live.run(source["run_id"]))["status"] == "running"
    point = "control.steer_committed" if winner == "steer" else "control.steer_prepared"
    admission = journey.arm("steer", point, role="control", run_id=source["run_id"])
    token, key = uuid4().hex, uuid4().hex
    path, body = f"/api/v1/runs/{source['run_id']}/steer", agent_input("LIVE_STEER " + token)
    steering = asyncio.create_task(live.http.post(path, json=body, headers={"Idempotency-Key": key}))
    try:
        await journey.reached(admission)
        assert not steering.done()
        rows = await journey.inbox(source["thread_id"])
        assert len(rows) == int(winner == "steer")
        if rows:
            assert rows[0]["status"] == "pending" and rows[0]["target_run_id"] == source["run_id"]
        if outcome == "cancelled":
            await journey.post(*await journey.command(source, "interrupt"), expected=202)
        else:
            journey.release(terminal)
        sealed = await live.finish(source["run_id"], outcome)
        assert sealed["failure"]
        journey.release(admission)
        reply = await steering
        assert reply.status_code == (202 if winner == "steer" else 409), reply.text
        final_rows = await journey.inbox(source["thread_id"])
        if winner == "steer":
            steer = reply.json()
            assert [(row["id"], row["status"], row["consumed_by_run_id"]) for row in final_rows] == [
                (steer["steer_id"], "superseded", None)
            ]
            assert await journey.post(path, body, key=key, expected=202) == steer
        else:
            assert final_rows == []
        journey.release(terminal)
        assert_absent(journey.observations(case), [token])
        assert journey.effects(case) == []
        assert await journey.thread_runs(source) == [sealed]
        assert all(
            attempt["status"] in {"failed", "cancelled"} for attempt in await journey.lab.attempts(source["run_id"])
        )
    finally:
        journey.release(terminal)
        journey.release(admission)
        steering.cancel()
        await asyncio.gather(steering, return_exceptions=True)


async def test_interrupt_after_committed_consumption_preserves_receipt_and_status(control):
    journey, live = control, control.live
    case = await journey.case(effect=True)
    tool = journey.arm("tool", "tool.after_effect", case_id=case["case_id"])
    source = await journey.start(case)
    await journey.reached(tool)
    steer, _ = await journey.steer(source["run_id"])
    consumed = journey.arm("consumed", "control.inbox_consumed", run_id=source["run_id"], receipts=1)
    journey.release(tool)
    await journey.reached(consumed)
    rows = await journey.inbox(source["thread_id"])
    state = await journey.state(source["run_id"])
    assert len(rows) == 1 and rows[0]["id"] == steer["steer_id"]
    assert rows[0]["status"] == "consumed" and rows[0]["consumed_by_run_id"] == source["run_id"]
    assert rows[0]["consumed_state_digest_sha256"] == state["digest"]
    assert rows[0]["consumed_checkpoint_seq"] == state["seq"]
    assert state["receipts"] == [{"inbox_entry_id": steer["steer_id"], "kind": "steer"}]
    assert (await live.run(source["run_id"]))["status"] == "running"
    await journey.post(*await journey.command(source, "interrupt"), expected=202)
    cancelled = await live.finish(source["run_id"], "cancelled")
    journey.release(consumed)
    assert await journey.inbox(source["thread_id"]) == rows
    status = await live.request("GET", f"/api/v1/runs/{source['run_id']}/steers/{steer['steer_id']}")
    assert status["status"] == "consumed" and status["consumed_by_run_id"] == source["run_id"]
    assert journey.effects(case) == [case["token"]]
    assert await journey.thread_runs(source) == [cancelled]
