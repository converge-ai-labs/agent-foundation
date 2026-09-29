"""Explicit deferred batches cross the HTTP, checkpoint and native tool-result boundaries."""

import json

import pytest

pytestmark = pytest.mark.anyio


async def mixed_wait(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    model = await runs_kit.create_model(service, scripted_model)
    agent = await runs_kit.add_agent(
        service,
        "mixed",
        model,
        user_questions=True,
        client_tools=[
            {
                "name": "review_invoice",
                "description": "Ask a human to check the invoice",
                "parameters_json_schema": {"type": "object"},
            }
        ],
        toolsets={"configuration": {"enabled": True}},
    )
    calls = [
        ("create_agent", {"name": "Must not create", "config": {"model": model}}, "approve"),
        ("review_invoice", {}, "review"),
        ("ask_user_question", {"questions": [runs_kit.QUESTION]}, "question"),
    ]
    scripted_model._script(
        {
            "deltas": [
                {
                    "tool_calls": [
                        {
                            "index": i,
                            "id": cid,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                        for i, (name, args, cid) in enumerate(calls)
                    ]
                }
            ],
            "interval": 0,
            "finish": "tool_calls",
            "gate": None,
            "to": None,
        }
    )
    first = await runs_kit.start_thread(service, agent, "Review the invoice and choose a color")
    await (await runs_kit.attempt(service))
    waiting = await runs_kit.get_run(service, first["run"]["id"])
    assert waiting["status"] == "waiting", waiting
    assert waiting["wait_reason"] == "multiple"
    assert [x["tool_call_id"] for x in waiting["pending"]["approvals"]] == ["approve"]
    assert {x["tool_call_id"] for x in waiting["pending"]["calls"]} == {"review", "question"}
    review = next(x for x in waiting["pending"]["calls"] if x["tool_call_id"] == "review")
    assert review["presentation"] is None
    return agent, waiting


def results():
    return {
        "approvals": {"approve": {"action": "deny", "reason": "Keep the current agent"}},
        "calls": {
            "review": {"status": "returned", "value": {"approved": False}},
            "question": {"status": "returned", "value": {"response": "Blue"}},
        },
    }


async def test_incomplete_or_misclassified_batches_leave_no_successor(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    _, waiting = await mixed_wait(service, scripted_model, runs_kit)
    for mistake in ["missing_approval", "missing_call", "unknown", "category", "old_envelope"]:
        body = results()
        if mistake == "missing_approval":
            body["approvals"] = {}
        elif mistake == "missing_call":
            del body["calls"]["review"]
        elif mistake == "unknown":
            body["calls"]["unknown"] = {"status": "returned", "value": None}
        elif mistake == "category":
            del body["calls"]["review"]
            body["approvals"]["review"] = {"action": "approve"}
        else:
            body = {"answers": []}
        response = await service.client.post(
            f"{service.api}/runs/{waiting['id']}/resume", json=body, headers=runs_kit.fresh_key()
        )
        assert response.status_code == 400, (mistake, response.text)
        thread = await runs_kit.get_thread(service, waiting["thread_id"])
        assert thread["head_run_id"] == waiting["id"] and thread["current_run_id"] is None, mistake
        runs = (await service.client.get(f"{service.api}/threads/{waiting['thread_id']}/runs")).json()["items"]
        assert len(runs) == 1, mistake


@pytest.mark.parametrize("failed", [False, True])
async def test_custom_human_results_and_native_failures_share_the_same_resume(
    service, scripted_model, runs_kit, failed
):  # type: ignore[no-untyped-def]
    _, waiting = await mixed_wait(service, scripted_model, runs_kit)
    body = results()
    if failed:
        body["calls"]["review"] = {"status": "failed", "message": "Reviewer unavailable"}
    response = await service.client.post(
        f"{service.api}/runs/{waiting['id']}/resume", json=body, headers=runs_kit.fresh_key()
    )
    assert response.status_code == 201, response.text
    assert response.json()["resume"]["calls"]["review"] == body["calls"]["review"]
    scripted_model.say("Review handled")
    await (await runs_kit.attempt(service))
    assert (await runs_kit.get_run(service, response.json()["id"]))["status"] == "completed"
    await scripted_model.request()
    request = await scripted_model.request()
    tool_results = {m["tool_call_id"]: m["content"] for m in request["messages"] if m["role"] == "tool"}
    assert set(tool_results) == {"approve", "review", "question"}
    assert (
        "Reviewer unavailable" in tool_results["review"]
        if failed
        else json.loads(tool_results["review"]) == {"approved": False}
    )
    assert "Keep the current agent" in tool_results["approve"]
    assert json.loads(tool_results["question"]) == {"answers": {}, "response": "Blue"}
    assert len(await runs_kit.inbox(service, waiting["thread_id"])) == 1


async def test_fork_discards_inherited_wait_only_in_the_new_branch(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    agent, waiting = await mixed_wait(service, scripted_model, runs_kit)
    response = await service.client.post(
        f"{service.api}/runs/{waiting['id']}/fork",
        json=runs_kit.message(agent, "Do something else"),
        headers=runs_kit.fresh_key(),
    )
    assert response.status_code == 201, response.text
    forked = response.json()["run"]
    scripted_model.say("Changed direction")
    await (await runs_kit.attempt(service))
    assert (await runs_kit.get_run(service, forked["id"]))["status"] == "completed"
    assert (await runs_kit.get_run(service, waiting["id"]))["pending"] == waiting["pending"]
    original = await runs_kit.get_thread(service, waiting["thread_id"])
    assert original["head_run_id"] == waiting["id"] and original["current_run_id"] is None
    await scripted_model.request()
    request = await scripted_model.request()
    tool_results = {m["tool_call_id"]: m["content"] for m in request["messages"] if m["role"] == "tool"}
    assert "No decision was given" in tool_results["approve"]
    assert all("No response was given" in tool_results[cid] for cid in ("review", "question"))
    assert request["messages"][-1]["role"] == "user"
    assert "Do something else" in str(request["messages"][-1]["content"])
    agents = (await service.client.get(f"{service.api}/agents", params={"source": "custom"})).json()["items"]
    assert [a["name"] for a in agents] == ["Mixed"]
