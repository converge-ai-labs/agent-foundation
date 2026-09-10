"""P1: lost Control replies and completion-time queue transaction failures."""

import asyncio
import signal
from uuid import uuid4

import pytest

from .management_packages import upload

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("point", ["accept.state_published", "accept.committed"])
async def test_control_crash_retries_same_submission_without_duplicate_run(run_faults, point):
    journey, live, lab = run_faults, run_faults.live, run_faults.lab
    await lab.stop(lab.workers[0])
    case = await journey.case(effect=True)
    barrier = journey.arm("control-reply", point, role="control")
    key = uuid4().hex
    body = {**live.start_body(case), "agent_id": journey.agent_id}
    pending = asyncio.create_task(live.http.post(journey.base + "/runs", headers={"Idempotency-Key": key}, json=body))
    try:
        reached = await journey.reached(barrier)
        runs = await journey.runs()
        assert len(runs) == (1 if point == "accept.committed" else 0)
        if runs:
            live.runs.append(runs[0]["id"])
            assert runs[0]["status"] == "accepted"
        await journey.restart_control()
        journey.release(barrier)
        reply = (await asyncio.gather(pending, return_exceptions=True))[0]
        assert isinstance(reply, Exception) or reply.status_code != 202, "The fault did not lose the acceptance reply"
        receipt = await journey.post(journey.base + "/runs", body, key=key, expected=202)
        live.track(receipt)
        if point == "accept.committed":
            assert receipt["run_id"] == reached["run_id"]
        else:
            assert (await live.http.get(f"/api/v1/runs/{reached['run_id']}")).status_code == 404
        assert await journey.post(journey.base + "/runs", body, key=key, expected=202) == receipt
        await lab.start_worker()
        result, attempts = await journey.assert_settled(receipt, case=case, effects=1)
        assert len(attempts) == 1 and len(await journey.runs()) == 1
        assert case["token"] in result["output_text"]
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("fault", ["crash_before_commit", "rollback", "lost_commit_response"])
async def test_queue_handoff_failure_preserves_atomic_consumption_and_single_successor(run_faults, fault):
    journey, live, lab = run_faults, run_faults.live, run_faults.lab
    case = await journey.case(effect=True)
    gate = journey.arm("source-model", "model.request", role="control", case_id=case["case_id"], request=2)
    source = await journey.start(case)
    await journey.reached(gate)
    later = await journey.case()
    queued = await journey.queue(source, later)
    path = f"/api/v1/queued-submissions/{queued['queued_submission_id']}"
    point = {
        "crash_before_commit": "queue.before_commit",
        "rollback": "queue.rollback",
        "lost_commit_response": "queue.after_commit",
    }[fault]
    barrier = journey.arm(
        "queue-commit",
        point,
        run_id=source["run_id"],
        action="pause" if fault == "crash_before_commit" else "unavailable",
    )
    if fault == "rollback":
        rolled_back = journey.arm("rolled-back", "queue.rolled_back", run_id=source["run_id"])
    journey.release(gate)
    reached = await journey.reached(barrier)
    if fault == "rollback":
        await journey.reached(rolled_back)
    if fault != "lost_commit_response":
        assert (await live.run(source["run_id"]))["status"] == "running"
        assert (await live.request("GET", path))["state"] == "queued"
        absent = await live.http.get(f"/api/v1/runs/{reached['successor_run_id']}")
        assert absent.status_code == 404, "Uncommitted successor escaped its transaction"
        if fault == "crash_before_commit":
            await lab.stop(lab.workers[0], signal.SIGKILL)
            journey.release(barrier)
            await lab.start_worker()
        else:
            journey.release(rolled_back)
    finished_source = await live.finish(source["run_id"])
    consumed = await live.wait(
        lambda: live.request("GET", path), lambda row: row["state"] != "queued", "queue recovery consumption"
    )
    assert consumed["state"] == "consumed" and consumed["consumed_run_id"]
    live.runs.append(consumed["consumed_run_id"])
    result = await live.finish(consumed["consumed_run_id"])
    assert result["parent_run_id"] == source["run_id"] and result["output_text"] == later["token"]
    runs = await live.collection(f"/api/v1/threads/{source['thread_id']}/runs")
    assert len(runs) == 2 and {run["id"] for run in runs} == {source["run_id"], result["id"]}
    assert journey.effects(case) == [case["token"]]
    assert len(journey.observations(case)) == 2 and len(journey.observations(later)) == 1
    assert await live.run(source["run_id"]) == finished_source
    await live.assert_stable(lambda: live.request("GET", path), consumed, seconds=1)


async def test_permanently_invalid_queue_head_fails_and_next_entry_progresses(run_faults):
    journey, live = run_faults, run_faults.live
    asset = await upload(
        journey, "assets", b"queued-input", params={"filename": "queued.txt", "media_type": "text/plain"}
    )
    environment, _ = await journey.environment()
    case = await journey.case()
    barrier = journey.arm("source", "model.request", role="control", case_id=case["case_id"], request=1)
    source = await journey.start(case)
    await journey.reached(barrier)
    bad_case = await journey.case()
    bad_input = live.start_body(bad_case)["input"]
    bad_input["content"].append(
        {"type": "binary", "source": {"type": "asset", "asset_id": asset["id"]}, "delivery": "environment_path"}
    )
    invalid = await journey.queue(source, bad_case, input=bad_input, environment={"environment_id": environment["id"]})
    later = await journey.case()
    valid = await journey.queue(source, later)
    assert (await live.http.delete(f"/api/v1/assets/{asset['id']}")).status_code == 204
    journey.release(barrier)
    await live.finish(source["run_id"])
    failed = await live.wait(
        lambda: live.request("GET", f"/api/v1/queued-submissions/{invalid['queued_submission_id']}"),
        lambda row: row["state"] != "queued",
        "permanent queue failure",
    )
    assert failed["state"] == "failed" and failed["failure"]["code"] == "asset_deleted"
    assert failed["consumed_run_id"] is None
    consumed = await live.wait(
        lambda: live.request("GET", f"/api/v1/queued-submissions/{valid['queued_submission_id']}"),
        lambda row: row["state"] != "queued",
        "next queued intent",
    )
    assert consumed["state"] == "consumed"
    live.runs.append(consumed["consumed_run_id"])
    result = await live.finish(consumed["consumed_run_id"])
    assert result["output_text"] == later["token"] and result["parent_run_id"] == source["run_id"]
    assert len(await live.collection(f"/api/v1/threads/{source['thread_id']}/runs")) == 2
    assert journey.observations(bad_case) == []
