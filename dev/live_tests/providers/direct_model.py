"""Shared assertions for direct cloud Model journeys through Control and Worker."""

from uuid import uuid4

from ..infrastructure.client import agent_input
from ..infrastructure.management_support import client_tool, feedback_body
from ..protocol.stream import assert_stream


async def submit(journey, agent, prompt):
    receipt = await journey.post(
        journey.base + "/runs",
        {"agent_id": agent["agent"]["id"], "input": agent_input(prompt)},
        expected=202,
    )
    journey.live.track(receipt)
    return receipt


async def continue_run(journey, parent, prompt):
    thread = await journey.live.thread(parent["thread_id"])
    receipt = await journey.post(
        f"/api/v1/runs/{parent['id']}/continue",
        {"expected_thread_version": thread["version"], "input": agent_input(prompt)},
        expected=202,
    )
    journey.live.track(receipt)
    child = await journey.live.finish(receipt["run_id"])
    assert child["parent_run_id"] == parent["id"] and child["thread_id"] == parent["thread_id"]
    assert await journey.live.run(parent["id"]) == parent
    return child


async def check_provider_discovery_and_model_test(configured, *, require_catalog_entry=True):
    journey, model, settings = configured
    provider_path = journey.base + "/model-providers/" + model["provider_id"]
    before = await journey.live.request("GET", provider_path)
    assert before["type"] == settings.provider and before["configuration"] == {}
    assert before["credential_configured"] and "credential" not in before
    probe = await journey.post(provider_path + "/test", {}, expected=200)
    assert probe["success"], probe["code"]
    catalog = await journey.post(provider_path + "/discover-models", {}, expected=200)
    ids = [item["upstream_model"] for item in catalog["items"]]
    assert ids and len(ids) == len(set(ids))
    if require_catalog_entry:
        assert model["upstream_model"] in ids
    description = await journey.post(
        provider_path + "/describe-model",
        {"upstream_model": model["upstream_model"], "model_api": model["model_api"]},
        expected=200,
    )
    assert description["settings_schema"] and description["suggested_model_api"] == model["model_api"]
    result = await journey.post(journey.base + "/models/" + model["id"] + "/test", {}, expected=200)
    assert result["success"] and result["code"] == "connection_succeeded", (result["code"], result["elapsed_ms"])
    assert await journey.live.request("GET", provider_path) == before


async def check_stream_usage_and_continuation(configured):
    journey, model, _settings = configured
    proof = "TEXT_" + uuid4().hex
    agent = await journey.agent(
        model_key=model["key"], instructions="Follow the user's instruction exactly. Preserve the conversation token."
    )
    receipt = await submit(journey, agent, "Remember this token and reply with only the token: " + proof)
    events = []
    async with journey.live.stream(receipt["run_id"]) as stream:
        async for event in stream:
            events.append(event)
    parent = await journey.live.finish(receipt["run_id"])
    assert parent["output_text"].strip() == proof
    assert_stream(events, parent["id"])
    assert any(event.kind == "agui.text_message_content" for event in events)
    completed = next(event for event in events if event.kind == "run.completed")
    usage = completed.data["payload"]["data"]["usage"]
    child = await continue_run(journey, parent, "Reply with only the token from the previous turn.")
    assert child["output_text"].strip() == proof
    assert usage["model_requests"] >= 1 and usage["input_tokens"] > 0 and usage["output_tokens"] > 0, usage


async def check_client_tool_structured_output_and_history(configured):
    journey, model, _settings = configured
    agent = await journey.agent(
        model={"model_key": model["key"], "settings": {"parallel_tool_calls": False}},
        instructions=(
            "If no live_client result exists in the conversation, call live_client once to obtain a proof value. "
            "Use the returned value as the exact answer in the structured output. "
            "If a result already exists, reuse that value without calling live_client again. Never invent a value."
        ),
        client_tools=[client_tool()],
        output_spec={
            "name": "proof",
            "schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
        },
    )
    receipt = await submit(journey, agent, "Obtain a proof using live_client and submit the structured result.")
    waiting = await journey.live.finish(receipt["run_id"], "waiting")
    assert waiting["wait_reason"] == "client_tool"
    proof = "TOOL_" + uuid4().hex  # Created only after the cloud model has called the tool.
    feedback = await feedback_body(journey.live, waiting, {"value": proof})
    successor = await journey.post(f"/api/v1/runs/{waiting['id']}/feedback", feedback, expected=202)
    journey.live.track(successor)
    parent = await journey.live.finish(successor["run_id"])
    assert parent["output"] == {"answer": proof}
    assert await journey.live.run(waiting["id"]) == waiting
    child = await continue_run(journey, parent, "Return the same structured proof from the existing tool result.")
    assert child["output"] == {"answer": proof}


async def check_rejection_and_repair(configured, fault):
    journey, model, settings = configured
    provider_path = journey.base + "/model-providers/" + model["provider_id"]
    model_path = journey.base + "/models/" + model["id"]
    agent = await journey.agent(model_key=model["key"], instructions="Reply with OK.")
    try:
        if fault == "invalid_credential":
            await journey.patch(provider_path, {"credential": "invalid-e2e-" + uuid4().hex})
        else:
            await journey.patch(model_path, {"upstream_model": "missing-e2e-" + uuid4().hex})
        tested = await journey.post(model_path + "/test", {}, expected=200)
        assert tested["success"] is False and tested["code"] == "connection_failed"
        receipt = await submit(journey, agent, "Say OK")
        failed = await journey.live.finish(receipt["run_id"], "failed")
        assert failed["failure"] and not failed["output_text"]
    finally:
        if fault == "invalid_credential":
            await journey.patch(provider_path, {"credential": settings.api_key.get_secret_value()})
        else:
            await journey.patch(model_path, {"upstream_model": model["upstream_model"]})
    tested = await journey.post(model_path + "/test", {}, expected=200)
    assert tested["success"], (tested["code"], tested["elapsed_ms"])
    repaired = await submit(journey, agent, "Say OK")
    assert (await journey.live.finish(repaired["run_id"]))["output_text"].strip()
    assert await journey.live.run(failed["id"]) == failed
