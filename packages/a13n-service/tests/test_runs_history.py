"""A thread's committed history: resuming a wait, forking a run, reading run items and session activity."""

import json
from dataclasses import replace

import pytest
from a13n_service.runs import display

pytestmark = pytest.mark.anyio

LOOKUP = {"name": "lookup", "description": "Look a value up", "parameters_json_schema": {"type": "object"}}
ANSWER = {"answers": [{"tool_call_id": "call_lookup", "action": "complete", "result": {"value": 42}}]}


async def _waiting(service, scripted_model, runs_kit, **config) -> dict:  # type: ignore[no-untyped-def]
    """A run sealed waiting after its first model turn, whose tool call the test scripted."""
    agent = await runs_kit.create_agent(service, scripted_model, **config)
    run_id = (await runs_kit.start_thread(service, agent, "look it up"))["run"]["id"]
    await (await runs_kit.attempt(service))
    run = await runs_kit.get_run(service, run_id)
    assert run["status"] == "waiting", run
    return run


async def test_a_resume_replays_its_key_and_answers_a_wait_once(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    scripted_model.call("lookup", {}, call_id="call_lookup")
    waiting = await _waiting(service, scripted_model, runs_kit, client_tools=[LOOKUP])
    resume = f"{service.workspace}/runs/{waiting['id']}/resume"
    stray = {"answers": [{"tool_call_id": "call_other", "action": "complete", "result": {"value": 1}}]}
    refused = await service.client.post(resume, json=stray, headers={"idempotency-key": "resume-0"})
    assert refused.status_code == 400 and refused.json()["error"]["details"] == {
        "field": "tool_call_id",
        "reason": "no_pending_call",
        "id": "call_other",
    }, refused.text

    first = await service.client.post(resume, json=ANSWER, headers={"idempotency-key": "resume-1"})
    assert first.status_code == 201, first.text
    replayed = await service.client.post(resume, json=ANSWER, headers={"idempotency-key": "resume-1"})
    assert replayed.status_code == 200 and replayed.json()["id"] == first.json()["id"], replayed.text
    other = {"answers": [{"tool_call_id": "call_lookup", "action": "complete", "result": {"value": 7}}]}
    reused = await service.client.post(resume, json=other, headers={"idempotency-key": "resume-1"})
    assert reused.status_code == 409 and reused.json()["error"]["details"]["reason"] == "idempotency_key_reused"
    again = await service.client.post(resume, json=ANSWER, headers={"idempotency-key": "resume-2"})
    assert again.status_code == 409 and again.json()["error"]["details"]["reason"] == "not_idle_waiting_head"


async def test_a_resume_closes_a_question_only_wait_without_answers(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    scripted_model.call("ask_user_question", {"questions": [runs_kit.QUESTION]}, call_id="call_ask")
    waiting = await _waiting(service, scripted_model, runs_kit, user_questions=True)
    assert waiting["wait_reason"] == "user_input"

    # Declining every question is an explicit decision: the successor continues with no response to them.
    resumed = await service.client.post(
        f"{service.workspace}/runs/{waiting['id']}/resume", json={"answers": []}, headers=runs_kit.fresh_key()
    )
    assert resumed.status_code == 201 and resumed.json()["resume"]["answers"][0]["action"] == "no_response"
    scripted_model.say("Moving on")
    await (await runs_kit.attempt(service))
    successor = await runs_kit.get_run(service, resumed.json()["id"])
    assert successor["status"] == "completed" and successor["trigger"] == "resume", successor
    await scripted_model.request()
    closed = await scripted_model.request()
    assert closed["messages"][-1]["role"] == "tool" and closed["messages"][-1]["tool_call_id"] == "call_ask"


async def test_a_fork_continues_a_runs_history_in_a_new_thread(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    origin = await runs_kit.start_thread(service, agent, "remember seven")
    scripted_model.say("Noted")
    await (await runs_kit.attempt(service))

    forked = await service.client.post(
        f"{service.workspace}/runs/{origin['run']['id']}/fork",
        json=runs_kit.message(agent, "what was it?"),
        headers=runs_kit.fresh_key(),
    )
    assert forked.status_code == 201, forked.text
    thread, run = forked.json()["thread"], forked.json()["run"]
    assert (thread["origin"], thread["origin_run_id"], thread["session_id"]) == (
        "fork",
        origin["run"]["id"],
        origin["thread"]["session_id"],
    )
    assert (run["lineage"], run["parent_run_id"]) == ("fork", origin["run"]["id"])
    scripted_model.say("Seven")
    await (await runs_kit.attempt(service))
    assert (await runs_kit.get_run(service, run["id"]))["output"] == "Seven"
    await scripted_model.request()
    continued = await scripted_model.request()
    # The fork reads the origin's exchange once, then its own message.
    messages = [json.dumps(message) for message in continued["messages"]]
    order = [next(i for i, message in enumerate(messages) if text in message) for text in ("seven", "Noted", "was it")]
    assert order == sorted(order) and sum("remember seven" in message for message in messages) == 1


class _MissingOnce:
    """An object store that misses one display object, as a read racing a checkpoint commit that replaced it."""

    def __init__(self, objects) -> None:  # type: ignore[no-untyped-def]
        self.objects, self.missed = objects, False

    async def put(self, key: str, data: bytes, *, content_type: str):  # type: ignore[no-untyped-def]
        return await self.objects.put(key, data, content_type=content_type)

    async def get(self, key: str) -> bytes | None:
        if "/display/" in key and not self.missed:
            self.missed = True
            return None
        return await self.objects.get(key)

    async def keys(self, prefix: str, *, limit: int, after: str | None = None) -> list[str]:
        return await self.objects.keys(prefix, limit=limit, after=after)

    async def delete(self, key: str) -> None:
        await self.objects.delete(key)


async def test_run_items_follow_a_replaced_display(service, scripted_model, runs_kit, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    scripted_model.say("Hello")
    await (await runs_kit.attempt(service))
    store = _MissingOnce(service.runtime.objects)
    monkeypatch.setattr(service.app.state, "runtime", replace(service.runtime, objects=store))
    listing = await runs_kit.items(service, run_id)
    assert store.missed and runs_kit.texts(listing) == [("user", "hi"), ("assistant", "Hello")]


async def test_a_new_run_keeps_the_session_etag(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    submitted = await runs_kit.start_thread(service, agent, "first")
    session = f"{service.workspace}/sessions/{submitted['thread']['session_id']}"
    before = await service.client.get(session)
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    await runs_kit.submit(service, submitted["thread"]["id"], runs_kit.message(agent, "second"))

    # The session's activity moved with the new run; its labels' ETag did not.
    after = await service.client.get(session)
    assert after.json()["preview"]["input_text"] == "second"
    labelled = await service.client.patch(
        session, json={"labels": {"topic": "colors"}}, headers={"if-match": before.headers["etag"]}
    )
    assert labelled.status_code == 200, labelled.text


async def test_sessions_list_most_recently_updated_first_and_refuse_malformed_filters(
    service, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    first, second = [await runs_kit.start_thread(service, agent, text) for text in ("first", "second")]
    sessions = f"{service.workspace}/sessions"
    page = (await service.client.get(sessions, params={"status": ["accepted"], "limit": 1})).json()
    assert [item["id"] for item in page["items"]] == [second["thread"]["session_id"]]
    rest = await service.client.get(
        sessions, params={"status": ["accepted"], "limit": 1, "cursor": page["next_cursor"]}
    )
    assert [item["id"] for item in rest.json()["items"]] == [first["thread"]["session_id"]], rest.text
    assert (await service.client.get(sessions, params={"status": ["failed"]})).json()["items"] == []
    # A cursor continues only the query that issued it.
    moved = await service.client.get(sessions, params={"status": ["failed"], "limit": 1, "cursor": page["next_cursor"]})
    assert (moved.status_code, moved.json()["error"]["code"]) == (400, "invalid_cursor")

    # A label edit updates the session too, so it moves to the top of the list.
    older = await service.client.get(f"{sessions}/{first['thread']['session_id']}")
    labelled = await service.client.patch(
        f"{sessions}/{first['thread']['session_id']}",
        json={"labels": {"topic": "colors"}},
        headers={"if-match": older.headers["etag"]},
    )
    assert labelled.status_code == 200 and labelled.headers["etag"] != older.headers["etag"], labelled.text
    listed = (await service.client.get(sessions)).json()["items"]
    assert [item["id"] for item in listed] == [first["thread"]["session_id"], second["thread"]["session_id"]]

    for malformed in (
        {"updated_after": "garbage"},
        {"agent_id": "a" * 100},
        {"label": [f"k{index}:v" for index in range(9)]},
        {"status": ["unknown"]},
        {"limit": 101},
    ):
        response = await service.client.get(sessions, params=malformed)
        assert (response.status_code, response.json()["error"]["code"]) == (400, "invalid_argument"), malformed


async def test_run_items_keep_the_newest_over_their_limit(service, scripted_model, runs_kit, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    scripted_model.say("Hello")
    await (await runs_kit.attempt(service))
    complete = await runs_kit.items(service, run_id)
    assert complete["dropped"] == 0 and len(complete["items"]) > 2

    # Over its item limit the display keeps the newest items and counts the others.
    monkeypatch.setattr(display, "MAX_ITEMS", 2)
    bounded = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    scripted_model.say("Hello")
    await (await runs_kit.attempt(service))
    listing = await runs_kit.items(service, bounded)
    assert listing["dropped"] == len(complete["items"]) - 2, listing
    assert [item["kind"] for item in listing["items"]] == [item["kind"] for item in complete["items"]][-2:]
