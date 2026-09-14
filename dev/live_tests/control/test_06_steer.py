"""Case 6: durable mid-tool input is consumed by the existing Run."""

from uuid import uuid4

import pytest

from ..infrastructure.client import agent_input

pytestmark = pytest.mark.anyio


async def test_steer_during_tool_execution(live):
    case = await live.case("steer")
    receipt = await live.start(case)
    run_id = receipt["run_id"]
    await live.wait_evidence(case, "tool_started")
    thread = await live.thread(receipt["thread_id"])
    token = uuid4().hex
    key = uuid4().hex
    path = f"/api/v1/runs/{run_id}/steer"
    body = agent_input(f"LIVE_STEER {token}")
    steer = await live.request("POST", path, expected=202, headers={"Idempotency-Key": key}, json=body)
    assert await live.request("POST", path, expected=202, headers={"Idempotency-Key": key}, json=body) == steer
    assert steer["run_id"] == run_id
    current = await live.thread(receipt["thread_id"])
    assert current["version"] == thread["version"] and current["current_run_id"] == run_id
    inbox_path = f"/__live__/threads/{receipt['thread_id']}/inbox"
    pending = (await live.request("GET", inbox_path))["items"]
    assert len(pending) == 1 and pending[0]["id"] == steer["steer_id"]
    assert pending[0]["status"] == "pending"
    assert (await live.evidence(case))["steer_observations"] == [[]]
    await live.release(case)
    run = await live.finish(run_id)
    assert run["output_text"] == token, "The model did not receive the new steer input"
    status = await live.request("GET", f"/api/v1/runs/{run_id}/steers/{steer['steer_id']}")
    assert status["status"] == "consumed"
    assert status["consumed_by_run_id"] == run_id
    assert status["consumed_state_digest_sha256"] and status["consumed_checkpoint_seq"] is not None
    assert len(await live.collection(f"/api/v1/threads/{run['thread_id']}/runs")) == 1
    rows = (await live.request("GET", inbox_path))["items"]
    assert len(rows) == 1 and rows[0]["id"] == steer["steer_id"]
    for field in ("status", "consumed_by_run_id", "consumed_state_digest_sha256", "consumed_checkpoint_seq"):
        assert rows[0][field] == status[field]
    evidence = await live.evidence(case)
    assert evidence["steer_observations"] == [[], [token]], "Steer must appear exactly once in the model context"
    assert evidence["model_requests"] == 2
    assert await live.request("POST", path, expected=202, headers={"Idempotency-Key": key}, json=body) == steer
    await live.assert_stable(lambda: live.request("GET", inbox_path), {"items": rows}, seconds=2)
    assert (await live.evidence(case))["steer_observations"] == [[], [token]]
