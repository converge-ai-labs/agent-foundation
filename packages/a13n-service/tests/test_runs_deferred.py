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


def single_answer(category, call_id, value):
    return {"approvals": {}, "calls": {}, category: {call_id: value}}


async def test_answers_persist_individually_and_concurrent_last_answers_resume_once(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    import asyncio

    _, waiting = await mixed_wait(service, scripted_model, runs_kit)
    url = f"{service.api}/runs/{waiting['id']}/answers"
    batch = results()
    approval = single_answer("approvals", "approve", batch["approvals"]["approve"])
    first = await service.client.post(url, json=approval, headers={"idempotency-key": "first"})
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "waiting" and first.json()["successor"] is None
    loaded = (await service.client.get(url)).json()
    assert loaded == first.json()
    assert loaded["answers"][0]["answer"] == approval
    assert loaded["answers"][0]["answered_by_id"] == waiting["principal_id"]
    thread = await runs_kit.get_thread(service, waiting["thread_id"])
    assert thread["current_run_id"] is None
    remaining = [single_answer("calls", key, value) for key, value in batch["calls"].items()]
    # Duplicate deliveries race with both of the remaining answers.
    responses = await asyncio.gather(
        *(
            service.client.post(url, json=answer, headers={"idempotency-key": f"last-{i % 2}"})
            for i, answer in enumerate(remaining * 2)
        )
    )
    assert sorted(response.status_code for response in responses) == [200, 200, 201, 201], [r.text for r in responses]
    collected = (await service.client.get(url)).json()
    assert collected["status"] == "resumed" and len(collected["answers"]) == 3
    successor = collected["successor"]
    assert successor["parent_run_id"] == waiting["id"] and successor["resumed_by_id"] == waiting["principal_id"]
    assert successor["resume"]["calls"]["question"]["value"] == {"answers": {}, "response": "Blue"}
    runs = (await service.client.get(f"{service.api}/threads/{waiting['thread_id']}/runs")).json()["items"]
    assert len(runs) == 2
    replay = await service.client.post(url, json=approval, headers={"idempotency-key": "first"})
    assert replay.status_code == 200 and replay.json()["successor"]["id"] == successor["id"]
    scripted_model.say("Answers received")
    await (await runs_kit.attempt(service))
    assert (await runs_kit.get_run(service, successor["id"]))["status"] == "completed"


async def test_saved_answers_reject_conflicts_and_batch_resume_cannot_override_them(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    _, waiting = await mixed_wait(service, scripted_model, runs_kit)
    url = f"{service.api}/runs/{waiting['id']}/answers"
    batch = results()
    approval = single_answer("approvals", "approve", batch["approvals"]["approve"])
    assert (await service.client.post(url, json=approval, headers={"idempotency-key": "saved"})).status_code == 201
    duplicate = await service.client.post(url, json=approval, headers=runs_kit.fresh_key())
    assert duplicate.status_code == 200
    changed = single_answer("approvals", "approve", {"action": "approve"})
    for key, reason in [("saved", "idempotency_key_reused"), ("changed", "answer_already_saved")]:
        response = await service.client.post(url, json=changed, headers={"idempotency-key": key})
        assert response.status_code == 409 and response.json()["error"]["details"]["reason"] == reason
    batch["approvals"]["approve"] = {"action": "approve"}
    response = await service.client.post(
        f"{service.api}/runs/{waiting['id']}/resume", json=batch, headers=runs_kit.fresh_key()
    )
    assert response.status_code == 409 and response.json()["error"]["details"]["reason"] == "answer_already_saved"
    response = await service.client.post(
        f"{service.api}/runs/{waiting['id']}/resume", json=results(), headers=runs_kit.fresh_key()
    )
    assert response.status_code == 201, response.text
    collected = (await service.client.get(url)).json()
    assert len(collected["answers"]) == 1 and collected["successor"]["id"] == response.json()["id"]
    stale = await service.client.post(
        url, json=single_answer("calls", "review", results()["calls"]["review"]), headers=runs_kit.fresh_key()
    )
    assert stale.status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"approvals": {}, "calls": {}},
        {"approvals": {"approve": {"action": "approve"}}, "calls": {"review": {"status": "returned", "value": None}}},
        {"approvals": {"review": {"action": "approve"}}, "calls": {}},
        {"approvals": {}, "calls": {"unknown": {"status": "returned", "value": None}}},
        {
            "approvals": {},
            "calls": {"question": {"status": "returned", "value": {"answers": {"wrong question": "blue"}}}},
        },
    ],
)
async def test_invalid_single_answers_store_nothing(service, scripted_model, runs_kit, body):  # type: ignore[no-untyped-def]
    _, waiting = await mixed_wait(service, scripted_model, runs_kit)
    url = f"{service.api}/runs/{waiting['id']}/answers"
    response = await service.client.post(url, json=body, headers=runs_kit.fresh_key())
    assert response.status_code == 400, response.text
    assert (await service.client.get(url)).json()["answers"] == []
    assert (await runs_kit.get_thread(service, waiting["thread_id"]))["current_run_id"] is None


async def test_archived_wait_replays_saved_answers_but_rejects_new_ones(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    _, waiting = await mixed_wait(service, scripted_model, runs_kit)
    url = f"{service.api}/runs/{waiting['id']}/answers"
    approval = single_answer("approvals", "approve", results()["approvals"]["approve"])
    assert (await service.client.post(url, json=approval, headers={"idempotency-key": "saved"})).status_code == 201
    thread = await service.client.get(f"{service.api}/threads/{waiting['thread_id']}")
    archived = await service.client.post(
        f"{service.api}/threads/{waiting['thread_id']}/archive", headers={"if-match": thread.headers["etag"]}
    )
    assert archived.status_code == 200, archived.text
    response = await service.client.post(
        url, json=single_answer("calls", "review", results()["calls"]["review"]), headers=runs_kit.fresh_key()
    )
    assert response.status_code == 409
    replay = await service.client.post(url, json=approval, headers={"idempotency-key": "saved"})
    assert replay.status_code == 200 and replay.json()["status"] == "closed"
    assert replay.json()["successor"] is None and len(replay.json()["answers"]) == 1


async def test_last_answer_and_successor_roll_back_together(service, scripted_model, runs_kit, monkeypatch):  # type: ignore[no-untyped-def]
    from a13n_service.infra.errors import ServiceError
    from a13n_service.runs import resume

    _, waiting = await mixed_wait(service, scripted_model, runs_kit)
    url = f"{service.api}/runs/{waiting['id']}/answers"
    batch = results()
    for category, call_id in [("approvals", "approve"), ("calls", "review")]:
        response = await service.client.post(
            url, json=single_answer(category, call_id, batch[category][call_id]), headers=runs_kit.fresh_key()
        )
        assert response.status_code == 201
    original = resume.start_run

    async def fail_after_acceptance(*args, **kwargs):
        await original(*args, **kwargs)
        raise ServiceError("unavailable", "Transient acceptance failure")

    final = single_answer("calls", "question", batch["calls"]["question"])
    with monkeypatch.context() as patch:
        patch.setattr(resume, "start_run", fail_after_acceptance)
        response = await service.client.post(url, json=final, headers={"idempotency-key": "last"})
    assert response.status_code == 503, response.text
    saved = (await service.client.get(url)).json()
    assert len(saved["answers"]) == 2 and saved["successor"] is None
    assert (await runs_kit.get_thread(service, waiting["thread_id"]))["current_run_id"] is None
    retry = await service.client.post(url, json=final, headers={"idempotency-key": "last"})
    assert retry.status_code == 201 and retry.json()["status"] == "resumed"


async def test_answer_conflicts_preserve_json_types_and_keys(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    _, waiting = await mixed_wait(service, scripted_model, runs_kit)
    url = f"{service.api}/runs/{waiting['id']}/answers"
    first = single_answer("calls", "review", {"status": "returned", "value": True})
    assert (await service.client.post(url, json=first, headers={"idempotency-key": "first"})).status_code == 201
    changed = single_answer("calls", "review", {"status": "returned", "value": 1})
    response = await service.client.post(url, json=changed, headers=runs_kit.fresh_key())
    assert response.status_code == 409 and response.json()["error"]["details"]["reason"] == "answer_already_saved"
    different_item = single_answer("approvals", "approve", {"action": "approve"})
    response = await service.client.post(url, json=different_item, headers={"idempotency-key": "first"})
    assert response.status_code == 409 and response.json()["error"]["details"]["reason"] == "idempotency_key_reused"
    batch = results()
    batch["calls"]["review"] = {"status": "returned", "value": 1}
    response = await service.client.post(
        f"{service.api}/runs/{waiting['id']}/resume", json=batch, headers=runs_kit.fresh_key()
    )
    assert response.status_code == 409


async def test_answer_reads_and_replays_recheck_current_permission(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    from a13n_service.infra.db import transaction
    from a13n_service.tenancy.tables import GrantRow
    from sqlalchemy import select

    _, waiting = await mixed_wait(service, scripted_model, runs_kit)
    url = f"{service.api}/runs/{waiting['id']}/answers"
    answer = single_answer("approvals", "approve", {"action": "approve"})
    assert (await service.client.post(url, json=answer, headers={"idempotency-key": "first"})).status_code == 201
    async with transaction(service.runtime.storage) as session:
        old = await session.scalar(select(GrantRow).where(GrantRow.principal_id == waiting["principal_id"]))
        assert old is not None
        viewer = GrantRow(
            id="grt_viewer_test",
            organization_id=old.organization_id,
            workspace_id=old.workspace_id,
            principal_id=old.principal_id,
            role="viewer",
            created_by_id=old.created_by_id,
        )
        await session.delete(old)
        await session.flush()
        session.add(viewer)
    assert (await service.client.get(url)).status_code == 200
    replay = await service.client.post(url, json=answer, headers={"idempotency-key": "first"})
    assert replay.status_code == 403


async def test_archive_races_with_the_final_answer_without_partial_acceptance(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    import asyncio

    _, waiting = await mixed_wait(service, scripted_model, runs_kit)
    url = f"{service.api}/runs/{waiting['id']}/answers"
    batch = results()
    for category, call_id in [("approvals", "approve"), ("calls", "review")]:
        assert (
            await service.client.post(
                url, json=single_answer(category, call_id, batch[category][call_id]), headers=runs_kit.fresh_key()
            )
        ).status_code == 201
    thread = await service.client.get(f"{service.api}/threads/{waiting['thread_id']}")
    final, archived = await asyncio.gather(
        service.client.post(
            url, json=single_answer("calls", "question", batch["calls"]["question"]), headers=runs_kit.fresh_key()
        ),
        service.client.post(
            f"{service.api}/threads/{waiting['thread_id']}/archive", headers={"if-match": thread.headers["etag"]}
        ),
    )
    assert final.status_code in (201, 409), final.text
    assert archived.status_code in (200, 412), archived.text
    saved = (await service.client.get(url)).json()
    if final.status_code == 201:
        assert len(saved["answers"]) == 3 and saved["successor"]["id"] == final.json()["successor"]["id"]
    else:
        assert archived.status_code == 200
        assert len(saved["answers"]) == 2 and saved["successor"] is None and saved["status"] == "closed"


async def test_collected_answer_size_is_bounded_before_the_last_write(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    _, waiting = await mixed_wait(service, scripted_model, runs_kit)
    url = f"{service.api}/runs/{waiting['id']}/answers"
    large = single_answer("calls", "review", {"status": "returned", "value": "x" * 261900})
    response = await service.client.post(url, json=large, headers=runs_kit.fresh_key())
    assert response.status_code == 201, response.text
    response = await service.client.post(
        url,
        json=single_answer("approvals", "approve", {"action": "deny", "reason": "y" * 1024}),
        headers=runs_kit.fresh_key(),
    )
    assert response.status_code == 400 and response.json()["error"]["details"]["reason"] == "resume_too_large", (
        response.text
    )
    saved = (await service.client.get(url)).json()
    assert len(saved["answers"]) == 1 and saved["successor"] is None
