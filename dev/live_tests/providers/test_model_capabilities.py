"""Real configured model management, deferred tools and structured output."""

from uuid import uuid4

import pytest

from ..infrastructure.client import agent_input
from ..infrastructure.management_support import client_tool, feedback_body

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("configured_provider", ["model"], indirect=True)
async def test_real_model_management_and_tool_grounded_structured_output(configured_provider):
    journey, model = configured_provider
    provider_path = journey.base + "/model-providers/" + model["provider_id"]
    before = await journey.live.request("GET", provider_path)
    assert (await journey.post(provider_path + "/test", {}, expected=200))["success"]
    catalog = await journey.post(provider_path + "/discover-models", {}, expected=200)
    assert any(item["upstream_model"] == model["upstream_model"] for item in catalog["items"])
    definitions = await journey.live.collection("/api/v1/model-provider-types")
    definition = next(item for item in definitions if item["type"] == before["type"])
    assert definition["settings_schemas"][model["model_api"]]
    assert (await journey.post(journey.base + "/models/" + model["id"] + "/test", {}, expected=200))["success"]
    assert await journey.live.request("GET", provider_path) == before
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    agent = await journey.agent(
        model={"model_key": model["key"], "settings": {"parallel_tool_calls": False, "max_tokens": 384}},
        instructions=(
            "First call the live_client tool to obtain a secret proof value. "
            "After receiving its result, submit the structured output with answer equal to that exact value. "
            "Never invent a proof or call the output tool before the client result."
        ),
        client_tools=[client_tool()],
        output_spec={"name": "proof", "schema": schema},
    )
    receipt = await journey.post(
        journey.base + "/runs",
        {"agent_id": agent["agent"]["id"], "input": agent_input("Obtain the proof using live_client.")},
        expected=202,
    )
    journey.live.track(receipt)
    waiting = await journey.live.finish(receipt["run_id"], "waiting")
    assert waiting["wait_reason"] == "client_tool"
    proof = "PROOF_" + uuid4().hex  # Generated only after the actual model calls the tool.
    pending = await journey.live.request("GET", f"/api/v1/runs/{waiting['id']}/pending-actions")
    assert len(pending["items"]) == 1, [(item["kind"], item.get("tool_name")) for item in pending["items"]]
    feedback = await feedback_body(journey.live, waiting, {"value": proof})
    successor = await journey.post(f"/api/v1/runs/{waiting['id']}/feedback", feedback, expected=202)
    journey.live.track(successor)
    completed = await journey.live.finish(successor["run_id"])
    assert completed["output"] == {"answer": proof}
    assert await journey.live.run(waiting["id"]) == waiting
