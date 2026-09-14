"""Actual native SDK wire protocols through both Model test and Worker execution."""

import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "provider,api,path",
    [
        ("openai", "openai.chat_completions", "chat/completions"),
        ("openai", "openai.responses", "responses"),
        ("anthropic", "anthropic.messages", "messages"),
        ("google_gemini", "google.generate_content", "streamGenerateContent"),
    ],
)
async def test_native_model_test_and_streaming_run(model_lab, provider, api, path):
    journey = model_lab
    case = await journey.model(provider_type=provider, api=api)
    result = await journey.post(journey.base + "/models/" + case["model"]["id"] + "/test", {}, expected=200)
    assert result["success"] and result["code"] == "connection_succeeded"
    run = await journey.invoke(case)
    assert run["output_text"] == case["answer"]
    completed = next(event for event in await journey.live.events(run["id"]) if event.kind == "run.completed")
    usage = completed.data["payload"]["data"]["usage"]
    assert (usage["model_requests"], usage["input_tokens"], usage["output_tokens"]) == (1, 20, 5)
    requests = journey.requests(case, inference=True)
    assert len(requests) == 2 and path in requests[-1]["path"]
    if provider != "google_gemini":
        assert not requests[0]["body"].get("stream") and requests[1]["body"]["stream"] is True
    else:
        assert "generateContent" in requests[0]["path"]


@pytest.mark.parametrize("api", ["openai.chat_completions", "openai.responses"])
async def test_native_client_tool_round_trip_uses_unseen_result(model_lab, api):
    import json
    from uuid import uuid4

    from ..infrastructure.management_support import client_tool, feedback_body

    journey = model_lab
    case = await journey.model(api=api, tool="live_client", arguments={"prompt": "Supply proof"})
    agent = await journey.agent(model_key=case["model"]["key"], client_tools=[client_tool()])
    waiting = await journey.invoke(case, agent=agent, expected="waiting")
    assert waiting["wait_reason"] == "client_tool"
    proof = uuid4().hex
    assert proof not in json.dumps(journey.requests(case))
    body = await feedback_body(journey.live, waiting, {"value": proof})
    receipt = await journey.post(f"/api/v1/runs/{waiting['id']}/feedback", body, expected=202)
    journey.live.track(receipt)
    completed = await journey.live.finish(receipt["run_id"])
    assert proof in completed["output_text"]
    assert len(journey.requests(case, inference=True)) == 2
    assert await journey.live.run(waiting["id"]) == waiting


@pytest.mark.parametrize("api", ["openai.chat_completions", "openai.responses"])
async def test_native_structured_output_preserves_schema(model_lab, api):
    journey = model_lab
    case = await journey.model(api=api, tool="$output")
    output = {"answer": case["answer"], "count": 1}
    journey.plan_model(case, arguments=output)
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}, "count": {"type": "integer", "minimum": 1}},
        "required": ["answer", "count"],
        "additionalProperties": False,
    }
    agent = await journey.agent(model_key=case["model"]["key"], output_spec={"name": "proof", "schema": schema})
    assert (await journey.invoke(case, agent=agent))["output"] == output
    assert len(journey.requests(case, inference=True)) == 1
