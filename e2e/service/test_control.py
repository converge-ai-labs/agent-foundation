"""Run control through the public API: interrupt, authority revocation, approval, steering and queued input."""

import pytest

from .api import expect, transcript
from .scripted import tool_results, user_texts

pytestmark = pytest.mark.anyio

CONFIGURATION = {"toolsets": {"configuration": {"enabled": True}}}


async def test_interrupt_cancels_a_running_run_once(stack) -> None:  # type: ignore[no-untyped-def]
    api, model = stack.api, stack.model
    agent = await api.create_agent("Helper", await api.create_model(model.base_url))
    await model.say("Never shown.", to="[poem]", hold="never")
    receipt = await api.start(agent, "[poem] Write a long poem")
    thread_id, run_id = receipt["thread"]["id"], receipt["run"]["id"]
    await model.arrived("[poem]", status="held")

    requested = expect(await api.interrupt(run_id), 200)
    assert requested["cancel_requested_at"] is not None
    repeated = expect(await api.interrupt(run_id), 200)
    assert repeated["cancel_requested_at"] == requested["cancel_requested_at"]
    run = await api.sealed(run_id)
    assert (run["status"], run["failure"]["code"]) == ("cancelled", "cancelled")
    assert expect(await api.interrupt(run_id), 200)["status"] == "cancelled"
    # The worker cancelled the model call in flight at once instead of waiting for its answer.
    assert [request["status"] for request in await model.arrived("[poem]", status="abandoned")] == ["abandoned"]
    items = await api.items(run_id)
    assert items["complete"] and transcript(items) == [("user", "[poem] Write a long poem")]

    # There is no retry: the next message starts from the unchanged baseline, and the poem is not asked again.
    await model.say("Hello again.", to="[after]")
    follow = (await api.send(thread_id, agent, "[after] Just say hello"))["run"]
    fresh = await api.sealed(follow["id"])
    assert (fresh["status"], fresh["lineage"], fresh["parent_run_id"]) == ("completed", "root", None)
    [request] = await model.requests("[after]")
    assert user_texts(request) == ["[after] Just say hello"]
    refused = await api.interrupt(fresh["id"])
    assert expect(refused, 409)["error"]["code"] == "conflict"


async def test_revoking_the_grant_stops_the_run(stack) -> None:  # type: ignore[no-untyped-def]
    api, model = stack.api, stack.model
    agent = await api.create_agent("Helper", await api.create_model(model.base_url))
    account = expect(await api.client.post(f"{api.path}/service-accounts", json={"name": "Runner"}), 201)
    issued = await api.client.post(f"{api.path}/service-accounts/{account['id']}/keys", json={"name": "journey"})
    await model.say("Never shown.", to="[revoke]", hold="never")
    # An API key acts in its own workspace, so its client names none.
    async with stack.client(authorization=f"Bearer {expect(issued, 201)['secret']}") as runner:
        started = await runner.post(
            "/api/v1/threads",
            json={"agent_id": agent["id"], "payload": {"content": [{"type": "text", "text": "[revoke] Keep going"}]}},
            headers={"idempotency-key": "revoke-1"},
        )
        run_id = expect(started, 201)["run"]["id"]
        await model.arrived("[revoke]", status="held")

        grants = expect(await api.client.get(f"{api.path}/grants"), 200)["items"]
        [grant] = [grant for grant in grants if grant["principal"]["id"] == account["id"]]
        expect(await api.client.delete(f"{api.path}/grants/{grant['id']}"), 204)
        run = await api.sealed(run_id)
        assert (run["status"], run["failure"]["code"]) == ("failed", "authority_revoked")
        assert (await runner.get(f"/api/v1/runs/{run_id}")).status_code in {401, 403}
    await model.arrived("[revoke]", status="abandoned")


async def test_an_approval_waits_and_resumes_once(stack) -> None:  # type: ignore[no-untyped-def]
    api, model = stack.api, stack.model
    model_key = await api.create_model(model.base_url)
    agent = await api.create_agent("Builder", model_key, **CONFIGURATION)
    config = {"model": model_key, "instructions": "Greet people."}
    await model.call("create_agent", {"name": "Greeter", "config": config}, call_id="call_create", to="[build]")

    async def greeters() -> list[dict]:
        return expect(await api.client.get("/api/v1/agents", params={"q": "Greeter"}), 200)["items"]

    receipt = await api.start(agent, "[build] Make a greeter")
    waiting = await api.sealed(receipt["run"]["id"])
    assert (waiting["status"], waiting["wait_reason"]) == ("waiting", "approval")
    assert [item["tool_call_id"] for item in waiting["pending"]["approvals"]] == ["call_create"]
    assert await greeters() == []

    await model.say("Created the greeter.", to="[build]")
    approve = {"approvals": {"call_create": {"action": "approve"}}, "calls": {}}
    successor = expect(await api.resume(waiting["id"], approve, key="approve-1"), 201)
    replayed = expect(await api.resume(waiting["id"], approve, key="approve-1"), 200)
    assert replayed["id"] == successor["id"]
    changed = await api.resume(
        waiting["id"], {"approvals": {"call_create": {"action": "deny"}}, "calls": {}}, key="approve-1"
    )
    assert expect(changed, 409)["error"]["code"] == "conflict"

    done = await api.sealed(successor["id"])
    assert (done["status"], done["output"], done["trigger"]) == ("completed", "Created the greeter.", "resume")
    assert done["parent_run_id"] == waiting["id"]
    [greeter] = await greeters()
    assert greeter["name"] == "Greeter"
    # The wait is answered: it is no longer the thread's head, so another resume conflicts even with a new key.
    stale = await api.resume(waiting["id"], approve, key="approve-2")
    assert expect(stale, 409)["error"]["code"] == "conflict"
    requests = await model.requests("[build]")
    assert len(requests) == 2 and len(tool_results(requests[1])) == 1
    assert greeter["id"] in tool_results(requests[1])[0]


async def test_a_steer_joins_the_running_run(stack) -> None:  # type: ignore[no-untyped-def]
    api, model = stack.api, stack.model
    agent = await api.create_agent("Helper", await api.create_model(model.base_url), **CONFIGURATION)
    # The steer arrives while the first request is outstanding and joins at the tool boundary that follows it.
    await model.call("find_resources", {"kind": "model"}, call_id="call_find", to="[steer]", hold="steer")
    await model.say("Answered both.", to="[steer]")
    receipt = await api.start(agent, "[steer] First question")
    thread_id, run_id = receipt["thread"]["id"], receipt["run"]["id"]
    await model.arrived("[steer]", status="held")
    steer = await api.send(thread_id, agent, "[steer] Also consider this")
    assert steer["run"] is None and steer["entry"]["status"] == "pending"

    await model.open("steer")
    run = await api.sealed(run_id)
    assert (run["status"], run["output"]) == ("completed", "Answered both.")
    entries = await api.inbox(thread_id)
    assert [(entry["status"], entry["assigned_run_id"]) for entry in entries] == [("consumed", run_id)] * 2
    assert [item["id"] for item in await api.runs(thread_id)] == [run_id]
    first, second = await model.requests("[steer]")
    assert user_texts(first) == ["[steer] First question"]
    assert user_texts(second) == ["[steer] First question", "[steer] Also consider this"]


async def test_messages_queue_behind_a_client_tool_wait(stack) -> None:  # type: ignore[no-untyped-def]
    api, model = stack.api, stack.model
    lookup = {"name": "lookup", "description": "Look a value up", "parameters_json_schema": {"type": "object"}}
    agent = await api.create_agent("Helper", await api.create_model(model.base_url), client_tools=[lookup])
    await model.call("lookup", {}, call_id="call_lookup", to="[wait]")
    receipt = await api.start(agent, "[wait] Look it up")
    thread_id = receipt["thread"]["id"]
    waiting = await api.sealed(receipt["run"]["id"])
    assert (waiting["status"], waiting["wait_reason"]) == ("waiting", "call")

    # A wait with a client-tool request continues only by resume; messages stay queued meanwhile.
    queued = await api.send(thread_id, agent, "[wait] Then summarize", delivery="next_run")
    assert queued["run"] is None and queued["entry"]["status"] == "pending"
    assert (await api.thread(thread_id))["head_run_id"] == waiting["id"]

    await model.say("It is 42.", to="[wait]")
    answer = {"approvals": {}, "calls": {"call_lookup": {"status": "returned", "value": {"value": 42}}}}
    successor = await api.sealed(expect(await api.resume(waiting["id"], answer, key="lookup-1"), 201)["id"])
    assert (successor["status"], successor["output"]) == ("completed", "It is 42.")
    await model.say("Summary: 42.", to="[wait]")
    summary = await api.sealed((await api.next_run(thread_id, successor["id"]))["id"])
    assert (summary["status"], summary["output"], summary["trigger"]) == ("completed", "Summary: 42.", "queued")
    assert (summary["parent_run_id"], summary["source_entry_id"]) == (successor["id"], queued["entry"]["id"])
    _, resumed, last = await model.requests("[wait]")
    assert "42" in tool_results(resumed)[0] and user_texts(resumed) == ["[wait] Look it up"]
    assert user_texts(last) == ["[wait] Look it up", "[wait] Then summarize"]
