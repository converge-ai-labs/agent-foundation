"""Waiting rollover, composite Continue, batch feedback, and first-request recovery."""

import json
import signal
from uuid import uuid4

import pytest

from ..infrastructure.client import agent_input
from .control_support import assert_absent, assert_tokens_once

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("arrival", ["before_waiting_seal", "after_waiting_seal"])
async def test_steer_on_either_side_of_waiting_seal_binds_only_to_direct_successor(control, arrival):
    journey, live = control, control.live
    agent = await journey.control_agent()
    case = await journey.case(steps=[{"tool": "live_client", "arguments": {"prompt": "Wait"}}])
    barrier = journey.arm("waiting-seal", "outcome.verified", kind="waiting")
    receipt = await journey.start(case, agent_id=agent["agent"]["id"])
    await journey.reached(barrier)
    assert (await live.run(receipt["run_id"]))["status"] == "running"
    if arrival == "before_waiting_seal":
        steer, token = await journey.steer(receipt["run_id"])
    journey.release(barrier)
    waiting = await live.finish(receipt["run_id"], "waiting")
    if arrival == "after_waiting_seal":
        steer, token = await journey.steer(waiting["id"])
    rows = await journey.inbox(waiting["thread_id"])
    assert len(rows) == 1 and rows[0]["id"] == steer["steer_id"]
    assert rows[0]["target_run_id"] is None and rows[0]["source_waiting_run_id"] == waiting["id"]
    pending = await live.request("GET", f"/api/v1/runs/{waiting['id']}/pending-actions")
    assert len(pending["items"]) == 1
    first = journey.arm("feedback-first", "model.request", role="control", case_id=case["case_id"], request=2)
    successor = await journey.accept(
        *await journey.command(waiting, "feedback", resolutions=await journey.approve(waiting))
    )
    await journey.reached(first)
    assert_absent(journey.observations(case), [token])
    rows = await journey.inbox(waiting["thread_id"])
    assert rows[0]["status"] == "pending" and rows[0]["target_run_id"] == successor["run_id"]
    journey.release(first)
    result = await live.finish(successor["run_id"])
    assert result["output_text"] == "STEERS:" + token
    assert_tokens_once(journey.observations(case)[-1], [token])
    row = (await journey.inbox(waiting["thread_id"]))[0]
    assert row["status"] == "consumed" and row["consumed_by_run_id"] == result["id"]
    assert row["consumed_state_digest_sha256"] and row["consumed_checkpoint_seq"] > 0
    assert await live.run(waiting["id"]) == waiting


async def test_waiting_continue_applies_defaults_and_new_input_before_inbox_without_consuming_queue(control):
    journey, live = control, control.live
    case, waiting = await journey.waiting(mixed=True)
    pending = (await live.request("GET", f"/api/v1/runs/{waiting['id']}/pending-actions"))["items"]
    later = await journey.case()
    queued = await journey.queue(waiting, later)
    queue_before = await journey.queued(waiting)
    _, token = await journey.steer(waiting["id"])
    new_input = uuid4().hex
    gate = journey.arm("first-composite", "model.request", role="control", case_id=case["case_id"], request=2)
    path, body = await journey.command(waiting, "waiting_continue", input=agent_input("NEW_INPUT " + new_input))
    key = uuid4().hex
    successor = await journey.accept(path, body, key=key)
    await journey.reached(gate)
    accepted = await live.run(successor["run_id"])
    assert accepted["input_kind"] == "waiting_continue" and accepted["parent_run_id"] == waiting["id"]
    assert accepted["input"]["input"] == {"structured_content": None, **body["input"]}
    resolutions = accepted["input"]["resolutions"]
    assert [item["call_id"] for item in resolutions] == [item["call_id"] for item in pending]
    assert {item["kind"]: item["outcome"] for item in resolutions} == {
        "approval": "reject",
        "client_tool": "no_response",
    }
    assert await journey.queued(waiting) == queue_before
    assert len(await journey.thread_runs(waiting)) == 2
    observed = journey.observations(case)
    assert_tokens_once(observed[1], [new_input])
    assert_absent(observed, [token])
    assert "no response" in json.dumps(observed[1]).lower()
    assert (await live.evidence(case))["approval_executions"] == 0
    assert (await journey.post(path, body, key=key, expected=202))["run"]["run_id"] == successor["run_id"]
    journey.release(gate)
    result = await live.finish(successor["run_id"])
    assert result["output_text"] == "STEERS:" + token
    assert_tokens_once(journey.observations(case)[-1], [token, new_input])
    following = await journey.finish_queue(queued)
    assert following["parent_run_id"] == result["id"] and following["output_text"] == later["token"]
    assert await live.run(waiting["id"]) == waiting


async def test_waiting_continue_rejects_stale_and_execution_override_requests_atomically(control):
    journey, live = control, control.live
    _, waiting = await journey.waiting()
    await journey.queue(waiting, await journey.case())
    await journey.steer(waiting["id"])
    thread, queue, inbox = (
        await live.thread(waiting["thread_id"]),
        await journey.queued(waiting),
        await journey.inbox(waiting["thread_id"]),
    )
    path, valid = await journey.command(waiting, "waiting_continue")
    invalid = [
        ({"expected_thread_version": thread["version"] + 1}, 409),
        ({"waiting_resolution": {"mode": "defaults", "sealed_state_digest_sha256": "0" * 64}}, 409),
        ({"agent_id": waiting["agent_id"]}, 400),
        ({"agent_revision_id": waiting["agent_revision_id"]}, 400),
        ({"expected_current_revision_id": waiting["agent_revision_id"]}, 400),
        ({"config_override": {"instructions": "Changed instructions"}}, 400),
        ({"environment": None}, 400),
    ]
    for override, expected in invalid:
        await journey.post(path, {**valid, **override}, expected=expected)
        assert await live.thread(thread["id"]) == thread
        assert await journey.queued(waiting) == queue
        assert await journey.inbox(thread["id"]) == inbox
        assert await journey.thread_runs(waiting) == [waiting]


@pytest.mark.parametrize("recovery", ["none", "before_first_response", "after_waiting_checkpoint"])
async def test_repeated_waiting_rolls_entire_fifo_through_replacement_attempt(control, recovery):
    journey, live, lab = control, control.live, control.lab
    case, waiting = await journey.waiting(rounds=2)
    first_steer, first_token = await journey.steer(waiting["id"])
    model = journey.arm("first-feedback", "model.request", role="control", case_id=case["case_id"], request=2)
    successor = await journey.accept(
        *await journey.command(waiting, "feedback", resolutions=await journey.approve(waiting))
    )
    await journey.reached(model)
    second_steer, second_token = await journey.steer(successor["run_id"])
    tokens = [first_token, second_token]
    assert_absent(journey.observations(case), tokens)
    if recovery == "before_first_response":
        await lab.stop(lab.workers[0], signal.SIGKILL)
        journey.release(model)
        await lab.start_worker()
    else:
        if recovery == "after_waiting_checkpoint":
            checkpoint = journey.arm("second-wait", "checkpoint.after", run_id=successor["run_id"], kind="waiting")
        journey.release(model)
        if recovery == "after_waiting_checkpoint":
            await journey.reached(checkpoint)
            await lab.stop(lab.workers[0], signal.SIGKILL)
            journey.release(checkpoint)
            await lab.start_worker()
    waiting_again = await live.finish(successor["run_id"], "waiting")
    assert_absent(journey.observations(case), tokens)
    rows = await journey.inbox(waiting["thread_id"])
    assert [row["id"] for row in rows] == [first_steer["steer_id"], second_steer["steer_id"]]
    assert all(
        row["status"] == "pending"
        and row["target_run_id"] is None
        and row["source_waiting_run_id"] == waiting_again["id"]
        for row in rows
    )
    before = len(journey.observations(case))
    final = await journey.accept(
        *await journey.command(waiting_again, "feedback", resolutions=await journey.approve(waiting_again))
    )
    result = await live.finish(final["run_id"])
    observed = journey.observations(case)
    assert_absent(observed[: before + 1], tokens)
    assert_tokens_once(observed[-1], tokens)
    assert result["output_text"] == "STEERS:" + ",".join(tokens)
    rows = await journey.inbox(waiting["thread_id"])
    assert all(row["status"] == "consumed" and row["consumed_by_run_id"] == result["id"] for row in rows)
    assert len(await lab.attempts(waiting_again["id"])) == (1 if recovery == "none" else 2)
    assert await live.run(waiting["id"]) == waiting and await live.run(waiting_again["id"]) == waiting_again


async def test_applied_waiting_continue_checkpoint_recovers_without_reapplying_input_or_effect(control):
    journey, live, lab = control, control.live, control.lab
    agent = await journey.control_agent()
    case = await journey.case()
    journey.plan(
        case,
        batches=[
            [{"tool": "live_client", "arguments": {"prompt": "Wait"}}],
            [{"tool": "live_fault_effect", "arguments": {"case_id": case["case_id"], "token": case["token"]}}],
        ],
    )
    root = await journey.start(case, agent_id=agent["agent"]["id"])
    waiting = await live.finish(root["run_id"], "waiting")
    _, steer_token = await journey.steer(waiting["id"])
    input_token = uuid4().hex
    effect = journey.arm("effect", "tool.after_effect", case_id=case["case_id"])
    successor = await journey.accept(
        *await journey.command(waiting, "waiting_continue", input=agent_input("NEW_INPUT " + input_token))
    )
    await journey.reached(effect)
    checkpoint = journey.arm("applied", "checkpoint.after", kind="progress", receipts=0)
    journey.release(effect)
    await journey.reached(checkpoint)
    assert journey.effects(case) == [case["token"]]
    assert_absent(journey.observations(case), [steer_token])
    assert_tokens_once(journey.observations(case)[1], [input_token])
    await lab.stop(lab.workers[0], signal.SIGKILL)
    journey.release(checkpoint)
    await lab.start_worker()
    result, attempts = await journey.assert_settled(successor, case=case, effects=1)
    assert len(attempts) == 2 and result["input_kind"] == "waiting_continue"
    assert result["output_text"] == "STEERS:" + steer_token
    assert_tokens_once(journey.observations(case)[-1], [steer_token, input_token])


@pytest.mark.parametrize("selection", ["empty", "client_null", "approval_only", "reversed_full"])
async def test_feedback_normalizes_entire_mixed_batch_and_preserves_null(control, selection):
    journey, live = control, control.live
    case, waiting = await journey.waiting(mixed=True)
    pending = (await live.request("GET", f"/api/v1/runs/{waiting['id']}/pending-actions"))["items"]
    assert {item["kind"] for item in pending} == {"approval", "client_tool"}
    supplied = []
    for item in pending:
        if item["kind"] == "client_tool" and selection in {"client_null", "reversed_full"}:
            supplied.append({"call_id": item["call_id"], "action": "complete", "result": None})
        if item["kind"] == "approval" and selection in {"approval_only", "reversed_full"}:
            supplied.append({"call_id": item["call_id"], "action": "approve"})
    path, body = await journey.command(waiting, "feedback", resolutions=list(reversed(supplied)))
    successor = await journey.accept(path, body)
    result = await live.finish(successor["run_id"])
    resolutions = result["input"]["resolutions"]
    assert [item["call_id"] for item in resolutions] == [item["call_id"] for item in pending]
    expected = {
        "approval": "approve" if selection in {"approval_only", "reversed_full"} else "reject",
        "client_tool": "complete" if selection in {"client_null", "reversed_full"} else "no_response",
    }
    assert {item["kind"]: item["outcome"] for item in resolutions} == expected
    assert next(item for item in resolutions if item["kind"] == "client_tool").get("result") is None
    assert (await live.evidence(case))["approval_executions"] == int(expected["approval"] == "approve")
    tools = [message for message in journey.observations(case)[1]["body"]["messages"] if message["role"] == "tool"]
    assert len(tools) == 2
    assert ("no response" in json.dumps(tools).lower()) == (expected["client_tool"] == "no_response")
    await journey.post(path, body, expected=409)
    assert await live.run(waiting["id"]) == waiting and len(await journey.thread_runs(waiting)) == 2


async def test_batch_feedback_invalid_requests_leave_all_pending_calls_unchanged(control):
    journey, live = control, control.live
    case, waiting = await journey.waiting(mixed=True)
    pending_path = f"/api/v1/runs/{waiting['id']}/pending-actions"
    pending = await live.request("GET", pending_path)
    client = next(item for item in pending["items"] if item["kind"] == "client_tool")
    path, valid = await journey.command(waiting, "feedback", resolutions=await journey.approve(waiting))
    thread = await live.thread(waiting["thread_id"])
    for override, expected in [
        ({"expected_thread_version": thread["version"] + 1}, 409),
        ({"sealed_state_digest_sha256": "0" * 64}, 409),
        ({"resolutions": [{"call_id": client["call_id"], "action": "complete"}]}, 400),
        ({"resolutions": [{"call_id": client["call_id"], "action": "approve"}]}, 400),
        ({"resolutions": [{"call_id": "unknown", "action": "complete", "result": None}]}, 400),
        ({"resolutions": valid["resolutions"] * 2}, 400),
    ]:
        await journey.post(path, {**valid, **override}, expected=expected)
        assert await live.thread(thread["id"]) == thread
        assert await live.request("GET", pending_path) == pending
        assert await journey.thread_runs(waiting) == [waiting]
        assert len(journey.observations(case)) == 1
        assert (await live.evidence(case))["approval_executions"] == 0
