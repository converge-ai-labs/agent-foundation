"""Case 8: approval feedback creates a successor; rejection prevents the tool effect."""

from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_approval_feedback(live, action):
    case = await live.case("approval")
    receipt = await live.start(case, approval=True)
    waiting = await live.finish(receipt["run_id"], "waiting")
    assert waiting["wait_reason"] == "approval"
    assert not (await live.evidence(case))["output"], "Approval-required tool ran before feedback"
    pending = await live.request("GET", f"/api/v1/runs/{waiting['id']}/pending-actions")
    assert len(pending["items"]) == 1 and pending["items"][0]["kind"] == "approval"
    digest = waiting.get("sealed_state_digest_sha256")
    assert digest, "Waiting Run must expose its sealed-state digest for Native feedback"
    thread = await live.thread(waiting["thread_id"])
    body = {
        "expected_thread_version": thread["version"],
        "sealed_state_digest_sha256": digest,
        "resolutions": [{"call_id": pending["items"][0]["call_id"], "action": action}],
    }
    key = uuid4().hex
    path = f"/api/v1/runs/{waiting['id']}/feedback"
    successor = await live.request("POST", path, expected=202, headers={"Idempotency-Key": key}, json=body)
    live.track(successor)
    assert await live.request("POST", path, expected=202, headers={"Idempotency-Key": key}, json=body) == successor
    run = await live.finish(successor["run_id"])
    assert run["id"] != waiting["id"] and run["parent_run_id"] == waiting["id"]
    assert run["thread_id"] == waiting["thread_id"] and run["input_kind"] == "waiting_feedback"
    assert await live.run(waiting["id"]) == waiting
    evidence = await live.evidence(case)
    assert evidence["output"] == (case["token"] if action == "approve" else "")
    assert evidence["approval_executions"] == (1 if action == "approve" else 0)
