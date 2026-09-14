"""Case 13: durable queue order, terminal-intent Retry and independent Fork."""

from uuid import uuid4

import pytest

from ..infrastructure.client import agent_input

pytestmark = pytest.mark.anyio


async def test_busy_thread_consumes_queued_submissions_in_order(round_two):
    live = round_two.client
    case = await live.case("gate")
    first = await live.start(case)
    await round_two.wait_evidence(case, "gate_ready", run_id=first["run_id"])
    cases = [await live.case("basic") for _ in range(2)]
    queued = []
    for later in cases:
        thread = await live.thread(first["thread_id"])
        response = await live.request(
            "POST",
            f"/api/v1/threads/{thread['id']}/runs",
            expected=202,
            headers={"Idempotency-Key": uuid4().hex},
            json={"expected_thread_version": thread["version"], "input": live.start_body(later)["input"]},
        )
        assert response["outcome"] == "queued" and response["run"] is None
        queued.append(response["queued_submission"])
    assert [item["position"] for item in queued] == [1, 2]
    assert len(await live.collection(f"/api/v1/threads/{first['thread_id']}/runs")) == 1
    await live.release(case)
    parent = await live.finish(first["run_id"])
    for submission, later in zip(queued, cases, strict=True):
        consumed = await live.wait(
            lambda submission=submission: live.request(
                "GET", f"/api/v1/queued-submissions/{submission['queued_submission_id']}"
            ),
            lambda value: value["state"] != "queued",
            "queue consumption",
        )
        assert consumed["state"] == "consumed" and consumed["consumed_run_id"]
        run_id = consumed["consumed_run_id"]
        live.runs.append(run_id)
        result = await live.finish(run_id)
        assert result["output_text"] == later["token"]
        assert result["parent_run_id"] == parent["id"]
        assert result["thread_id"] == first["thread_id"] and result["session_id"] == first["session_id"]
        parent = result
    runs = await live.collection(f"/api/v1/threads/{first['thread_id']}/runs")
    assert len(runs) == 3 and len({run["id"] for run in runs}) == 3


async def test_retry_creates_new_run_with_original_intent(round_two):
    live = round_two.client
    case = await live.case("model_error")
    receipt = await live.start(case)
    failed = await live.finish(receipt["run_id"], "failed")
    attempts = await round_two.attempts(failed["id"])
    await live.release(case)  # Repair only the upstream; the failed Run's frozen intent is unchanged.
    thread = await live.thread(failed["thread_id"])
    retry = await live.request(
        "POST",
        f"/api/v1/runs/{failed['id']}/retry",
        expected=202,
        headers={"Idempotency-Key": uuid4().hex},
        json={"expected_thread_version": thread["version"]},
    )
    live.track(retry)
    result = await live.finish(retry["run_id"])
    assert result["id"] != failed["id"] and result["retry_of_run_id"] == failed["id"]
    for key in ("thread_id", "session_id", "input", "agent_revision_id"):
        assert result[key] == failed[key]
    assert result["output_text"] == case["token"]
    assert await live.run(failed["id"]) == failed
    assert await round_two.attempts(failed["id"]) == attempts


async def test_fork_retains_history_in_a_new_thread(round_two):
    live = round_two.client
    case = await live.case("remember")
    first = await live.start(case)
    parent = await live.finish(first["run_id"])
    original_thread = await live.thread(first["thread_id"])
    fork = await live.request(
        "POST",
        f"/api/v1/runs/{parent['id']}/fork",
        expected=202,
        headers={"Idempotency-Key": uuid4().hex},
        json={"input": agent_input("Recall the remembered token.")},
    )
    live.track(fork)
    result = await live.finish(fork["run_id"])
    assert result["thread_id"] != parent["thread_id"] and result["session_id"] == parent["session_id"]
    assert result["parent_run_id"] == parent["id"] and result["lineage_kind"] == "fork"
    assert result["output_text"] == case["token"]
    assert await live.run(parent["id"]) == parent
    assert await live.thread(parent["thread_id"]) == original_thread
