"""Case 6: durable mid-tool input is consumed by the existing Run."""

from uuid import uuid4

import pytest

from .client import agent_input

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
    await live.release(case)
    run = await live.finish(run_id)
    assert run["output_text"] == token, "The model did not receive the new steer input"
    status = await live.request("GET", f"/api/v1/runs/{run_id}/steers/{steer['steer_id']}")
    assert status["status"] == "consumed"
    assert status["consumed_by_run_id"] == run_id
    assert status["consumed_state_digest_sha256"] and status["consumed_checkpoint_seq"] is not None
    assert len(await live.collection(f"/api/v1/threads/{run['thread_id']}/runs")) == 1
