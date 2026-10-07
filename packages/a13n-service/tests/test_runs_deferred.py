"""Explicit deferred batches cross the HTTP, checkpoint and native tool-result boundaries."""

import json

import pytest

pytestmark = pytest.mark.anyio


async def mixed_wait(service, scripted_model, runs_kit, *, options=None):  # type: ignore[no-untyped-def]
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
    first = await runs_kit.start_thread(service, agent, "Review the invoice and choose a color", options=options or {})
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
        assert thread["last_run_id"] == waiting["id"] and thread["current_run_id"] is None, mistake
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
    assert original["last_run_id"] == waiting["id"] and original["current_run_id"] is None
    await scripted_model.request()
    request = await scripted_model.request()
    tool_results = {m["tool_call_id"]: m["content"] for m in request["messages"] if m["role"] == "tool"}
    assert "No decision was given" in tool_results["approve"]
    assert all("No response was given" in tool_results[cid] for cid in ("review", "question"))
    assert request["messages"][-1]["role"] == "user"
    assert "Do something else" in str(request["messages"][-1]["content"])
    agents = (await service.client.get(f"{service.api}/agents", params={"source": "custom"})).json()["items"]
    assert [a["name"] for a in agents] == ["Mixed"]


async def test_application_resume_ignores_retired_answer_storage(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    from a13n_service.infra.db import short_session, transaction
    from a13n_service.runs.tables import PendingAnswerRow

    _, waiting = await mixed_wait(service, scripted_model, runs_kit)
    legacy = {"approvals": {"approve": {"action": "approve"}}, "calls": {}}
    async with transaction(service.runtime.storage) as session:
        session.add(
            PendingAnswerRow(
                run_id=waiting["id"],
                tool_call_id="approve",
                workspace_id=waiting["workspace_id"],
                answer=legacy,
                answered_by_id=waiting["principal_id"],
                request_key="retired-answer",
                request_digest="0" * 64,
            )
        )
    # Retired collection routes cannot store another answer or initiate execution.
    url = f"{service.api}/runs/{waiting['id']}/answers"
    assert (await service.client.get(url)).status_code == 404
    assert (await service.client.post(url, json=legacy, headers=runs_kit.fresh_key())).status_code == 404
    thread = await runs_kit.get_thread(service, waiting["thread_id"])
    assert thread["current_run_id"] is None
    # The application's complete batch is authoritative, even when legacy storage differs.
    response = await service.client.post(
        f"{service.api}/runs/{waiting['id']}/resume", json=results(), headers=runs_kit.fresh_key()
    )
    assert response.status_code == 201, response.text
    assert response.json()["resume"]["approvals"] == results()["approvals"]
    async with short_session(service.runtime.storage) as session:
        stored = await session.get(PendingAnswerRow, (waiting["id"], "approve"))
        assert stored is not None and stored.answer == legacy
