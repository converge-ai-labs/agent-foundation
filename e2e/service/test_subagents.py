"""An async subagent: its child run executes on its own thread and reports back to the parent thread."""

import pytest

from .api import eventually, expect
from .scripted import user_texts

pytestmark = pytest.mark.anyio


async def test_an_async_child_reports_its_result_to_the_parent_thread(stack) -> None:  # type: ignore[no-untyped-def]
    api, model = stack.api, stack.model
    model_key = await api.create_model(model.base_url)
    helper = await api.create_agent("Worker", model_key, instructions="Role: worker")
    coordinator = await api.create_agent(
        "Coordinator",
        model_key,
        instructions="Role: coordinator",
        subagent_mode="async",
        subagents={"helper": {"agent_id": helper["id"], "description": "Computes answers"}},
    )
    await model.call(
        "delegate",
        {"subagent_name": "helper", "prompt": "Compute the answer"},
        call_id="call_d",
        to="Role: coordinator",
    )
    await model.say("Delegated to the helper.", to="Role: coordinator")
    await model.say("42", to="Role: worker")
    receipt = await api.start(coordinator, "[parent] Ask the helper")
    parent_thread = receipt["thread"]
    parent = await api.sealed(receipt["run"]["id"])
    assert (parent["status"], parent["output"]) == ("completed", "Delegated to the helper.")

    async def child_thread() -> dict | None:
        listing = await api.client.get("/api/v1/threads", params={"session_id": parent_thread["session_id"]})
        children = [thread for thread in expect(listing, 200)["items"] if thread["origin"] == "child"]
        return children[0] if children and children[0]["last_run_id"] else None

    child = await eventually(child_thread)
    assert (child["origin_thread_id"], child["origin_run_id"]) == (parent_thread["id"], parent["id"])
    assert child["origin_tool_call_id"] == "call_d"
    child_run = await api.sealed(child["last_run_id"])
    assert (child_run["status"], child_run["output"], child_run["trigger"]) == ("completed", "42", "spawned")

    # The child's result is delivered to the parent thread as its own entry and starts the parent's next run.
    await model.say("The helper said 42.", to="Role: coordinator")
    delivered = await api.sealed((await api.next_run(parent_thread["id"], parent["id"]))["id"])
    assert (delivered["status"], delivered["output"]) == ("completed", "The helper said 42.")
    assert (delivered["trigger"], delivered["parent_run_id"]) == ("child_result", parent["id"])
    entries = await api.inbox(parent_thread["id"])
    assert [(entry["kind"], entry["status"]) for entry in entries] == [
        ("message", "consumed"),
        ("child_result", "consumed"),
    ]
    assert entries[1]["child_run_id"] == child_run["id"]
    final = user_texts((await model.requests("Role: coordinator"))[-1])
    assert final[0] == "[parent] Ask the helper"
    assert '"output": "42"' in final[-1] and '"subagent": "helper"' in final[-1]
