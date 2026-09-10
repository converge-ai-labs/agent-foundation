"""Claim/terminal boundaries, exact preconditions, and lost successor replies."""

import asyncio
from uuid import uuid4

import pytest

from .client import agent_input
from .control_support import assert_tokens_once

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("boundary", ["before_claim", "after_claim"])
@pytest.mark.parametrize("action", ["steer", "interrupt"])
async def test_control_serializes_with_first_worker_claim(control, boundary, action):
    journey, live = control, control.live
    barrier = journey.arm("claim", "control." + boundary)
    case = await journey.case(effect=True)
    receipt = await journey.start(case)
    await journey.reached(barrier)
    run = await live.run(receipt["run_id"])
    assert run["status"] == ("accepted" if boundary == "before_claim" else "running")
    assert journey.observations(case) == [] and journey.effects(case) == []
    thread = await live.thread(receipt["thread_id"])
    if action == "steer":
        steer, token = await journey.steer(run["id"])
        assert await live.thread(thread["id"]) == thread
    else:
        path, body = await journey.command(run, "interrupt")
        await journey.post(path, body, expected=202)
        assert (await live.finish(run["id"], "cancelled"))["output_text"] is None
    journey.release(barrier)
    if action == "steer":
        result, attempts = await journey.assert_settled(receipt, case=case, effects=1)
        assert len(attempts) == 1 and result["output_text"] == "STEERS:" + token
        assert_tokens_once(journey.observations(case)[0], [token])
        rows = await journey.inbox(thread["id"])
        assert [(row["id"], row["status"]) for row in rows] == [(steer["steer_id"], "consumed")]
    else:
        following = await journey.start(await journey.case())
        await live.finish(following["run_id"])
        assert journey.observations(case) == [] and journey.effects(case) == []
        attempts = await journey.lab.attempts(run["id"])
        assert len(attempts) == (0 if boundary == "before_claim" else 1)
        assert all(attempt["status"] == "cancelled" for attempt in attempts)


async def test_completed_seal_wins_before_late_control_acceptance(control):
    journey, live = control, control.live
    receipt = await journey.start(await journey.case())
    completed = await live.finish(receipt["run_id"])
    thread = await live.thread(receipt["thread_id"])
    for action in ("steer", "interrupt"):
        path, body = await journey.command(completed, action)
        if action == "steer":
            body = agent_input("Too late for this Run")
        await journey.post(path, body, expected=409)
    assert await journey.inbox(thread["id"]) == []
    assert await live.thread(thread["id"]) == thread
    assert await journey.thread_runs(completed) == [completed]


async def test_stale_interrupt_versions_fail_without_retry_or_side_effect(control):
    journey, live = control, control.live
    before = journey.arm("before-claim", "control.before_claim")
    after = journey.arm("after-claim", "control.after_claim")
    case = await journey.case(effect=True)
    receipt = await journey.start(case)
    await journey.reached(before)
    path, stale = await journey.command(receipt, "interrupt")
    journey.release(before)
    await journey.reached(after)
    running = await live.run(receipt["run_id"])
    thread = await live.thread(receipt["thread_id"])
    assert running["version"] > stale["expected_run_version"]
    for body in (stale, {"expected_run_version": running["version"], "expected_thread_version": thread["version"] + 1}):
        await journey.post(path, body, expected=409)
        assert await live.run(running["id"]) == running
        assert await live.thread(thread["id"]) == thread
        assert journey.effects(case) == []
    journey.release(after)
    await journey.assert_settled(receipt, case=case, effects=1)


@pytest.mark.parametrize("operation", ["continue", "fork", "retry", "feedback", "waiting_continue"])
@pytest.mark.parametrize("window", ["state_published", "committed"])
async def test_successor_response_loss_replays_exact_acceptance(control, operation, window):
    journey, live, lab = control, control.live, control.lab
    if operation in {"feedback", "waiting_continue"}:
        _, source = await journey.waiting()
    else:
        case = await live.case("model_error") if operation == "retry" else await journey.case()
        receipt = await journey.start(case)
        source = await live.finish(receipt["run_id"], "failed" if operation == "retry" else "completed")
        if operation == "retry":
            await live.release(case)
    await lab.stop(lab.workers[0])
    source_thread = await live.thread(source["thread_id"])
    path, body = await journey.command(source, operation)
    point = (
        "control.state_published"
        if window == "state_published"
        else ("control.branch_committed" if operation == "fork" else "control.advance_committed")
    )
    barrier = journey.arm("lost-successor-reply", point, role="control")
    key = uuid4().hex
    request = asyncio.create_task(live.http.post(path, json=body, headers={"Idempotency-Key": key}))
    try:
        hit = await journey.reached(barrier)
        candidate = await live.http.get(f"/api/v1/runs/{hit['run_id']}")
        assert candidate.status_code == (404 if window == "state_published" else 200)
        await journey.restart_control()
        journey.release(barrier)
        lost = (await asyncio.gather(request, return_exceptions=True))[0]
        assert isinstance(lost, Exception) or lost.status_code != 202
        response = await journey.post(path, body, expected=202, key=key)
        assert await journey.post(path, body, expected=202, key=key) == response
        successor = response.get("run") if "outcome" in response else response
        live.track(successor)
        assert (successor["run_id"] == hit["run_id"]) == (window == "committed")
        assert len(await journey.runs()) == 2
        if operation == "fork":
            assert await live.thread(source["thread_id"]) == source_thread
        else:
            assert (await live.thread(source["thread_id"]))["version"] == source_thread["version"] + 1
        await lab.start_worker()
        await live.finish(successor["run_id"])
        assert await live.run(source["id"]) == source
    finally:
        journey.release(barrier)
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.parametrize("state", ["accepted", "running", "waiting", "completed", "failed", "cancelled"])
async def test_control_rejects_ineligible_current_run_states(control, state):
    journey, live = control, control.live
    barrier = None
    if state == "waiting":
        _, source = await journey.waiting()
    else:
        if state in {"accepted", "running", "cancelled"}:
            barrier = journey.arm("state", "control.before_claim" if state != "running" else "control.after_claim")
        case = await live.case("model_error") if state == "failed" else await journey.case()
        receipt = await journey.start(case)
        if barrier:
            await journey.reached(barrier)
            if state == "cancelled":
                await journey.post(*await journey.command(receipt, "interrupt"), expected=202)
            source = await live.run(receipt["run_id"])
        else:
            source = await live.finish(receipt["run_id"], state)
    allowed = {
        "accepted": {"steer", "interrupt"},
        "running": {"steer", "interrupt"},
        "waiting": {"steer", "feedback", "waiting_continue"},
        "completed": {"continue", "fork"},
        "failed": {"retry"},
        "cancelled": {"retry"},
    }[state]
    before = await live.thread(source["thread_id"])
    for operation in {"continue", "fork", "retry", "steer", "interrupt"} - allowed:
        path, body = await journey.command(source, operation)
        if operation == "steer":
            body = agent_input("Ineligible control")
        await journey.post(path, body, expected=409)
        assert await live.run(source["id"]) == source
        assert await live.thread(source["thread_id"]) == before
        assert await journey.inbox(source["thread_id"]) == []
    assert len(await journey.runs()) == 1
    if barrier:
        journey.release(barrier)
