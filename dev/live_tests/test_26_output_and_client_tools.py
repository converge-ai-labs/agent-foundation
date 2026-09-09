"""Case 26: structured-output validation and one deferred client-tool successor."""

from uuid import uuid4

import pytest

from .management_support import client_tool, feedback_body

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("invalid_arguments", "invalid_responses"),
    [
        pytest.param(None, 0, id="valid"),
        pytest.param("[]", 1, id="object-retry"),
        pytest.param("[]", 2, id="object-retry-exhausted"),
        pytest.param('{"answer":"invalid","count":0}', 1, id="schema-retry"),
        pytest.param('{"answer":"invalid","count":0}', 2, id="schema-retry-exhausted"),
    ],
)
async def test_structured_output_enforces_schema_and_retry_budget(management, invalid_arguments, invalid_responses):
    journey, live = management, management.live
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}, "count": {"type": "integer", "minimum": 1}},
        "required": ["answer", "count"],
        "additionalProperties": False,
    }
    agent = await journey.agent(output_spec={"name": "live_result", "schema": schema}, retries={"output": 1})
    case = await journey.case()
    # Non-objects and JSON Schema violations consume the same native retry budget.
    output = {"answer": case["token"], "count": 1}
    steps = [{"tool": "$output", "raw_arguments": invalid_arguments} for _ in range(invalid_responses)]
    if invalid_responses < 2:
        steps.append({"tool": "$output", "arguments": output})
    journey.plan(case, steps=steps)
    receipt = await journey.start(case, agent_id=agent["agent"]["id"])
    result = await live.finish(receipt["run_id"], "failed" if invalid_responses == 2 else "completed")
    observed = journey.observations(case)
    assert len(observed) == min(invalid_responses + 1, 2), "Native output retry budget was not preserved"
    tools = [entry["function"] for entry in observed[0]["body"]["tools"]]
    output_tool = next(tool for tool in tools if "answer" in tool["parameters"].get("properties", {}))
    assert set(output_tool["parameters"]["required"]) == {"answer", "count"}
    assert output_tool["parameters"]["properties"]["count"]["minimum"] == 1
    if invalid_responses < 2:
        assert result["output"] == output
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
