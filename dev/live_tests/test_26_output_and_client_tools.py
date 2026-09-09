"""Case 26: output schema enforcement and exactly one deferred client-tool successor."""

from uuid import uuid4

import pytest

from .management_support import client_tool, feedback_body

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("valid_after_retry", [True, False])
async def test_output_schema_is_enforced_by_harness(management, valid_after_retry):
    journey, live = management, management.live
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}, "count": {"type": "integer", "minimum": 1}},
        "required": ["answer", "count"],
        "additionalProperties": False,
    }
    agent = await journey.agent(output_spec={"name": "live_result", "schema": schema}, retries={"output": 1})
    case = await journey.case()
    correct = {"answer": case["token"], "count": 2}
    journey.plan(
        case,
        steps=[
            {"tool": "$output", "arguments": {"answer": case["token"], "count": 0}},
            {"tool": "$output", "arguments": correct if valid_after_retry else {"count": "invalid"}},
        ],
    )
    receipt = await journey.start(case, agent_id=agent["agent"]["id"])
    result = await live.finish(receipt["run_id"], "completed" if valid_after_retry else "failed")
    observed = journey.observations(case)
    assert len(observed) == 2, "Output validation did not perform exactly one model retry"
    tools = [entry["function"] for entry in observed[0]["body"]["tools"]]
    output_tool = next(tool for tool in tools if "answer" in tool["parameters"].get("properties", {}))
    assert set(output_tool["parameters"]["required"]) == {"answer", "count"}
    assert output_tool["parameters"]["properties"]["count"]["minimum"] == 1
    if valid_after_retry:
        assert result["output"] == correct
    else:
        assert result["failure"] and result["output"] is None


async def test_client_tool_feedback_rejects_invalid_duplicate_and_stale_results(management):
    journey, live = management, management.live
    agent = await journey.agent(client_tools=[client_tool()])
    case = await journey.case(steps=[{"tool": "live_client", "arguments": {"prompt": "Supply a fresh value"}}])
    receipt = await journey.start(case, agent_id=agent["agent"]["id"])
    waiting = await live.finish(receipt["run_id"], "waiting")
    assert waiting["wait_reason"] == "client_tool"
    assert len(journey.observations(case)) == 1
    value = uuid4().hex  # Not known to the model before the client submits it.
    body = await feedback_body(live, waiting, {"value": value})
    path = f"/api/v1/runs/{waiting['id']}/feedback"
    for overrides, status in (
        ({"sealed_state_digest_sha256": "0" * 64}, 409),
        ({"resolutions": [{"call_id": "unknown", "action": "complete", "result": {}}]}, 400),
        ({"resolutions": [{"call_id": body["resolutions"][0]["call_id"], "action": "approve"}]}, 400),
        ({"resolutions": body["resolutions"] * 2}, 400),
    ):
        await journey.post(path, {**body, **overrides}, expected=status)
        assert await live.run(waiting["id"]) == waiting
        assert len(await live.collection(f"/api/v1/threads/{waiting['thread_id']}/runs")) == 1
    key = uuid4().hex
    successor = await journey.post(path, body, expected=202, key=key)
    live.track(successor)
    assert await journey.post(path, body, expected=202, key=key) == successor
    result = await live.finish(successor["run_id"])
    assert value in result["output_text"]
    assert result["parent_run_id"] == waiting["id"] and result["input_kind"] == "waiting_feedback"
    assert len(journey.observations(case)) == 2
    await journey.post(path, body, expected=409)
    assert len(await live.collection(f"/api/v1/threads/{waiting['thread_id']}/runs")) == 2
    assert await live.run(waiting["id"]) == waiting
