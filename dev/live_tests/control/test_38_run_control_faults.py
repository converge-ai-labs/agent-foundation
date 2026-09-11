"""P0: control input, durable receipt and completion races across process loss."""

import json
import signal

import pytest

pytestmark = pytest.mark.anyio


def assert_steer_once(observations, token):
    counts = [
        sum(
            json.dumps(message.get("content")).count("LIVE_STEER " + token)
            for message in observed["body"]["messages"]
            if message.get("role") == "user"
        )
        for observed in observations
    ]
    assert counts[-1] == 1 and max(counts) == 1, counts


@pytest.mark.parametrize("point", ["checkpoint.before", "checkpoint.after"])
async def test_worker_loss_around_inbox_receipt_publication_preserves_single_consumption(run_faults, point):
    journey, live, lab = run_faults, run_faults.live, run_faults.lab
    case = await journey.case(effect=True, idempotent=True)
    tool = journey.arm("tool", "tool.after_effect", case_id=case["case_id"])
    receipt = await journey.start(case)
    await journey.reached(tool)
    steer, token = await journey.steer(receipt["run_id"])
    barrier = journey.arm("receipt", point, run_id=receipt["run_id"], receipts=1)
    journey.release(tool)
    await journey.reached(barrier)
    rows = await journey.inbox(receipt["thread_id"])
    assert len(rows) == 1 and rows[0]["status"] == "pending"
    state = await journey.state(receipt["run_id"])
    assert bool(state["receipts"]) == (point == "checkpoint.after")
    await lab.stop(lab.workers[0], signal.SIGKILL)
    journey.release(barrier)
    await lab.start_worker()
    result, attempts = await journey.assert_settled(receipt, case=case, effects=1)
    assert len(attempts) == 2 and result["output_text"] == "STEERS:" + token
    rows = await journey.inbox(receipt["thread_id"])
    assert len(rows) == 1 and rows[0]["id"] == steer["steer_id"]
    assert rows[0]["status"] == "consumed" and rows[0]["consumed_by_run_id"] == result["id"]
    assert rows[0]["consumed_state_digest_sha256"] and rows[0]["consumed_checkpoint_seq"] > 0
    assert_steer_once(journey.observations(case), token)
    assert len(await live.collection(f"/api/v1/threads/{receipt['thread_id']}/runs")) == 1


async def test_steer_after_terminal_candidate_continues_same_run_before_sealing(run_faults):
    journey, live = run_faults, run_faults.live
    case = await journey.case(effect=True)
    barrier = journey.arm("terminal", "outcome.verified", kind="completed", fence=1)
    receipt = await journey.start(case)
    await journey.reached(barrier)
    assert (await journey.state(receipt["run_id"]))["kind"] == "completed"
    assert (await live.run(receipt["run_id"]))["status"] == "running"
    steer, token = await journey.steer(receipt["run_id"])
    journey.release(barrier)
    result, attempts = await journey.assert_settled(receipt, case=case, effects=1)
    assert len(attempts) == 2 and attempts[0]["status"] == "succeeded"
    assert attempts[1]["start_reason"] == "pending_input"
    assert result["output_text"] == "STEERS:" + token
    rows = await journey.inbox(receipt["thread_id"])
    assert len(rows) == 1 and rows[0]["id"] == steer["steer_id"] and rows[0]["status"] == "consumed"
    assert_steer_once(journey.observations(case), token)
    assert len(await live.collection(f"/api/v1/threads/{receipt['thread_id']}/runs")) == 1


@pytest.mark.parametrize("window", ["receipt", "completed"])
async def test_interrupt_wins_after_object_publication_without_consuming_stale_receipt(run_faults, window):
    journey, live = run_faults, run_faults.live
    case = await journey.case(effect=True)
    tool = journey.arm("tool", "tool.after_effect", case_id=case["case_id"])
    receipt = await journey.start(case)
    await journey.reached(tool)
    if window == "receipt":
        steer, _ = await journey.steer(receipt["run_id"])
        barrier = journey.arm("receipt", "checkpoint.after", run_id=receipt["run_id"], receipts=1)
    else:
        barrier = journey.arm("terminal", "outcome.verified", run_id=receipt["run_id"])
    journey.release(tool)
    await journey.reached(barrier)
    assert await live.interrupt(receipt["run_id"])
    cancelled = await live.finish(receipt["run_id"], "cancelled")
    journey.release(barrier)
    following = await journey.start(await journey.case())
    await live.finish(following["run_id"])
    assert await live.run(receipt["run_id"]) == cancelled
    assert journey.effects(case) == [case["token"]], "Interrupt does not undo a completed external effect"
    if window == "receipt":
        rows = await journey.inbox(receipt["thread_id"])
        assert len(rows) == 1 and rows[0]["id"] == steer["steer_id"]
        assert rows[0]["status"] == "superseded" and rows[0]["consumed_by_run_id"] is None


async def test_async_results_arriving_after_candidate_defer_parent_seal(run_faults):
    journey, live, lab = run_faults, run_faults.live, run_faults.lab
    case = await live.case("async_children")
    root = lab.root / "workspace" / case["case_id"]
    (root / "parent_mode").write_text("completed")
    receipt = await journey.start(case, agent_id=live.config["async_agent_id"])
    await lab.wait_evidence(case, "parent_ready", run_id=receipt["run_id"])
    for _ in range(2):
        await lab.start_worker()
    for index in (0, 1):
        await lab.wait_evidence(case, f"child_{index}_started")
    threads = await live.collection(f"/api/v1/sessions/{receipt['session_id']}/threads")
    children = [thread["current_run_id"] for thread in threads if thread["origin_run_id"] == receipt["run_id"]]
    assert len(children) == 2
    live.runs.extend(children)
    barrier = journey.arm("parent-candidate", "outcome.verified", run_id=receipt["run_id"], fence=1)
    (root / "parent_release").touch()
    await journey.reached(barrier)
    (root / "children_release").touch()
    for child_id in children:
        await live.finish(child_id)
    rows = await live.wait(lambda: journey.inbox(receipt["thread_id"]), lambda rows: len(rows) == 2, "child inbox")
    assert all(row["status"] == "pending" and row["target_run_id"] == receipt["run_id"] for row in rows)
    journey.release(barrier)
    result, attempts = await journey.assert_settled(receipt)
    assert result["output_text"] == "children-seen:0,1"
    assert len(attempts) == 2 and attempts[1]["start_reason"] == "pending_input"
    rows = await journey.inbox(receipt["thread_id"])
    assert len(rows) == 2 and all(row["status"] == "consumed" for row in rows)
    assert {row["consumed_by_run_id"] for row in rows} == {receipt["run_id"]}
    assert len(await live.collection(f"/api/v1/threads/{receipt['thread_id']}/runs")) == 1
    for observed in (await live.evidence(case))["result_deliveries"]:
        assert len(observed) == len(set(observed))
