"""Case 1: acceptance, actual execution, durable result, and usage."""

import pytest

from ..protocol.stream import assert_stream

pytestmark = pytest.mark.anyio


async def test_basic_run(live):
    case = await live.case("basic")
    receipt = await live.start(case)
    run = await live.finish(receipt["run_id"])
    assert run["output_text"] == case["token"]
    assert run["agent_id"] == live.config["agent_id"]
    assert run["started_at"] and run["completed_at"]
    attempts = await live.collection(f"/api/v1/runs/{run['id']}/attempts")
    assert len(attempts) == 1, "The no-failure journey unexpectedly retried execution"
    assert attempts[0]["status"] == "succeeded"
    assert attempts[0]["harness_run_id"] and attempts[0]["finished_at"]
    events = await live.events(run["id"])
    assert_stream(events, run["id"])
    kinds = {event.kind for event in events}
    assert {"run.accepted", "run.running", "run.completed"} <= kinds
    completed = next(event for event in events if event.kind == "run.completed")
    assert completed.data["payload"]["data"]["usage"]["model_requests"] >= 1
    items = await live.collection(f"/api/v1/runs/{run['id']}/items")
    assistant_items = [
        item
        for item in items
        if item["kind"] == "text_message"
        and item["state"] == "completed"
        and any(
            event["event_type"] == "agui.text_message_start" and event["payload"]["role"] == "assistant"
            for event in item["content"]["events"]
        )
    ]
    assert len(assistant_items) == 1
    retained_text = "".join(
        event["payload"]["delta"]
        for event in assistant_items[0]["content"]["events"]
        if event["event_type"] == "agui.text_message_content"
    )
    assert retained_text == case["token"]
    assert (await live.thread(run["thread_id"]))["head_run_id"] == run["id"]
