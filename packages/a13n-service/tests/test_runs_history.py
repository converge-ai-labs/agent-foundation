"""A thread's committed history: resuming a wait, forking a run, reading run items and session activity."""

import asyncio
import json
from dataclasses import replace

import pytest
from a13n_harness.recovery import INTERRUPTED_TOOL_RESULT
from a13n_service.infra.db import short_session
from a13n_service.runs.accept import ThreadAdvancer, advance
from a13n_service.runs.tables import RunItemPageRow
from sqlalchemy import select

pytestmark = pytest.mark.anyio

LOOKUP = {"name": "lookup", "description": "Look a value up", "parameters_json_schema": {"type": "object"}}
ANSWER = {"approvals": {}, "calls": {"call_lookup": {"status": "returned", "value": {"value": 42}}}}


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
    resume = f"{service.api}/runs/{waiting['id']}/resume"
    stray = {"approvals": {}, "calls": {"call_other": {"status": "returned", "value": {"value": 1}}}}
    refused = await service.client.post(resume, json=stray, headers={"idempotency-key": "resume-0"})
    assert refused.status_code == 400 and refused.json()["error"]["details"] == {
        "field": "calls",
        "reason": "pending_coverage_mismatch",
        "missing": ["call_lookup"],
        "unexpected": ["call_other"],
    }, refused.text

    first = await service.client.post(resume, json=ANSWER, headers={"idempotency-key": "resume-1"})
    assert first.status_code == 201, first.text
    replayed = await service.client.post(resume, json=ANSWER, headers={"idempotency-key": "resume-1"})
    assert replayed.status_code == 200 and replayed.json()["id"] == first.json()["id"], replayed.text
    other = {"approvals": {}, "calls": {"call_lookup": {"status": "returned", "value": {"value": 7}}}}
    reused = await service.client.post(resume, json=other, headers={"idempotency-key": "resume-1"})
    assert reused.status_code == 409 and reused.json()["error"]["details"]["reason"] == "idempotency_key_reused"
    again = await service.client.post(resume, json=ANSWER, headers={"idempotency-key": "resume-2"})
    assert again.status_code == 409 and again.json()["error"]["details"]["reason"] == "not_idle_waiting_run"


async def test_a_resume_explicitly_skips_a_question(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    scripted_model.call("ask_user_question", {"questions": [runs_kit.QUESTION]}, call_id="call_ask")
    waiting = await _waiting(service, scripted_model, runs_kit, user_questions=True)
    assert waiting["wait_reason"] == "call"

    # Declining every question is an explicit decision: the successor continues with no response to them.
    resumed = await service.client.post(
        f"{service.api}/runs/{waiting['id']}/resume",
        json={"approvals": {}, "calls": {"call_ask": {"status": "failed", "message": "User chose not to answer"}}},
        headers=runs_kit.fresh_key(),
    )
    assert resumed.status_code == 201 and resumed.json()["resume"]["calls"]["call_ask"]["status"] == "failed"
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
        f"{service.api}/runs/{origin['run']['id']}/fork",
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


async def test_a_cancelled_runs_history_continues_with_its_open_calls_interrupted(
    executing, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    service = executing
    agent = await runs_kit.delegating(service, scripted_model, "inline")
    scripted_model.call(
        "delegate", {"subagent": "helper", "prompt": "compute"}, call_id="call_d", to="Role: coordinator"
    )
    gate = asyncio.Event()
    scripted_model.say("too late", gate=gate, to="Role: worker")
    started = await runs_kit.start_thread(service, agent, "ask the helper")
    thread_id, run_id = started["thread"]["id"], started["run"]["id"]
    await scripted_model.request()
    # The worker's request means the delegate call is committed and executing.
    await scripted_model.request()
    interrupted = await service.client.post(f"{service.api}/runs/{run_id}/interrupt")
    assert interrupted.status_code == 200, interrupted.text
    assert (await runs_kit.sealed(service, run_id))["status"] == "cancelled"
    gate.set()
    assert (await runs_kit.get_thread(service, thread_id))["last_run_id"] == run_id

    async def continued(text: str) -> tuple[list[object], bool]:
        """The next request's tool results after the delegate call, and whether a later message holds `text`."""
        messages = (await scripted_model.request())["messages"]
        start = next(i for i, message in enumerate(messages) if message.get("tool_calls"))
        results = [json.loads(message["content"]) for message in messages[start + 1 :] if message["role"] == "tool"]
        return results, any(text in str(message["content"]) for message in messages[start + 1 :])

    # The thread and a fork both continue from the cancelled run's checkpoint; neither repeats the delegation.
    interrupted_only = ([{"error": INTERRUPTED_TOOL_RESULT}], True)
    scripted_model.say("Continued", to="Role: coordinator")
    submitted = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "what happened?"))
    assert submitted.status_code == 201, submitted.text
    successor = await runs_kit.sealed(service, submitted.json()["run"]["id"])
    assert (successor["status"], successor["parent_run_id"]) == ("completed", run_id), successor
    assert await continued("what happened?") == interrupted_only
    scripted_model.say("Forked", to="Role: coordinator")
    forked = await service.client.post(
        f"{service.api}/runs/{run_id}/fork", json=runs_kit.message(agent, "and here?"), headers=runs_kit.fresh_key()
    )
    assert forked.status_code == 201, forked.text
    assert (await runs_kit.sealed(service, forked.json()["run"]["id"]))["status"] == "completed"
    assert await continued("and here?") == interrupted_only


class _MissingOnce:
    """An object store that misses one tail object, as a read racing a checkpoint commit that replaced it."""

    def __init__(self, objects) -> None:  # type: ignore[no-untyped-def]
        self.objects, self.missed = objects, False

    async def put(self, key: str, data: bytes, *, content_type: str):  # type: ignore[no-untyped-def]
        return await self.objects.put(key, data, content_type=content_type)

    async def get(self, key: str) -> bytes | None:
        if "/tail/" in key and not self.missed:
            self.missed = True
            return None
        return await self.objects.get(key)

    async def keys(self, prefix: str, *, limit: int, after: str | None = None) -> list[str]:
        return await self.objects.keys(prefix, limit=limit, after=after)

    async def delete(self, key: str) -> None:
        await self.objects.delete(key)


async def test_run_items_follow_a_replaced_tail(service, scripted_model, runs_kit, monkeypatch) -> None:  # type: ignore[no-untyped-def]
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
    session = f"{service.api}/sessions/{submitted['thread']['session_id']}"
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
    sessions = f"{service.api}/sessions"
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


async def test_run_items_page_by_ordinal_across_pages_and_the_tail(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model, toolsets={"configuration": {"tools": {"find": {}}}})
    for number in range(16):
        scripted_model.call("find_resources", {"kind": "model"}, call_id=f"call_{number}")
    scripted_model.say("Found them")
    run_id = (await runs_kit.start_thread(service, agent, "find"))["run"]["id"]
    settings = service.runtime.settings
    worker = settings.worker.model_copy(update={"page_items": 16})
    runtime = replace(service.runtime, settings=settings.model_copy(update={"worker": worker}))
    await (await runs_kit.attempt(service, runtime=runtime))

    newest = await runs_kit.items(service, run_id)
    count = len(newest["items"])
    assert [item["ordinal"] for item in newest["items"]] == list(range(1, count + 1)) and count > 32, newest
    async with short_session(service.runtime.storage) as session:
        pages = (
            await session.execute(
                select(RunItemPageRow.first_ordinal, RunItemPageRow.last_ordinal)
                .where(RunItemPageRow.run_id == run_id)
                .order_by(RunItemPageRow.first_ordinal)
            )
        ).all()
    assert [tuple(page) for page in pages] == [(first, first + 15) for first in range(1, count - 15, 16)]

    async def window(**params: int) -> list[int]:
        response = await service.client.get(f"{service.api}/runs/{run_id}/items", params=params)
        assert response.status_code == 200, response.text
        return [item["ordinal"] for item in response.json()["items"]]

    # The newest items, then each earlier window, until the first item.
    windows, before = [await window(limit=10)], None
    while (before := windows[-1][0]) > 1:
        windows.append(await window(before=before, limit=10))
    assert [ordinal for part in reversed(windows) for ordinal in part] == list(range(1, count + 1))
    assert await window(after=10, limit=20) == list(range(11, 31))
    assert await window(after=count) == []
    both = await service.client.get(f"{service.api}/runs/{run_id}/items", params={"before": 5, "after": 1})
    assert both.status_code == 400 and both.json()["error"]["details"]["field"] == "before"


async def test_invalid_question_results_leave_the_wait_unchanged(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    scripted_model.call("ask_user_question", {"questions": [runs_kit.QUESTION]}, call_id="call_ask")
    waiting = await _waiting(service, scripted_model, runs_kit, user_questions=True)
    for result in [
        {},
        {"response": "  "},
        {"answers": {"Another question?": "blue"}},
        {"answers": {"Which color?": ["red", "blue"]}},
        {"response": "blue", "extra": "not allowed"},
    ]:
        refused = await service.client.post(
            f"{service.api}/runs/{waiting['id']}/resume",
            json={"approvals": {}, "calls": {"call_ask": {"status": "returned", "value": result}}},
            headers=runs_kit.fresh_key(),
        )
        assert refused.status_code == 400, (result, refused.text)
        assert refused.json()["error"]["details"] == {
            "field": "calls",
            "reason": "invalid_question_response",
            "id": "call_ask",
        }, result
        thread = await runs_kit.get_thread(service, waiting["thread_id"])
        assert thread["last_run_id"] == waiting["id"] and thread["current_run_id"] is None, result
        assert len(await runs_kit.inbox(service, waiting["thread_id"])) == 1, result


@pytest.mark.parametrize("same_key", [False, True])
async def test_question_resume_arbitrates_concurrent_replies_and_inbox_scans(
    service, scripted_model, runs_kit, same_key
) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model, user_questions=True)
    original = await runs_kit.start_thread(service, agent, "pick a color")
    thread_id, run_id = original["thread"]["id"], original["run"]["id"]
    scripted_model.call("ask_user_question", {"questions": [runs_kit.QUESTION]}, call_id="call_ask")
    await (await runs_kit.attempt(service))
    key = runs_kit.fresh_key()
    answers = {"approvals": {}, "calls": {"call_ask": {"status": "returned", "value": {"response": "blue"}}}}
    first, second, message, _, _ = await asyncio.gather(
        service.client.post(f"{service.api}/runs/{run_id}/resume", json=answers, headers=key),
        service.client.post(
            f"{service.api}/runs/{run_id}/resume", json=answers, headers=key if same_key else runs_kit.fresh_key()
        ),
        runs_kit.submit(service, thread_id, runs_kit.message(agent, "unrelated", delivery="next_run")),
        advance(service.runtime, thread_id),
        ThreadAdvancer(service.runtime, batch=1)(),
    )
    assert sorted([first.status_code, second.status_code]) == ([200, 201] if same_key else [201, 409])
    winner = first if first.status_code == 201 else second
    if same_key:
        assert first.json()["id"] == second.json()["id"]
    assert message.status_code == 201 and message.json()["entry"]["status"] == "pending"
    assert (await runs_kit.get_thread(service, thread_id))["current_run_id"] == winner.json()["id"]
    runs = (await service.client.get(f"{service.api}/threads/{thread_id}/runs")).json()["items"]
    assert len(runs) == 2 and sum(run["trigger"] == "resume" for run in runs) == 1
