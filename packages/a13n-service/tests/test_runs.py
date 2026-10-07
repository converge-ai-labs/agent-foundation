"""Runs end to end: HTTP submission, a worker executing through the real model adapter, checkpoints, seal."""

import asyncio
import json
from dataclasses import replace
from datetime import datetime
from typing import Any

import pytest
from a13n_service.infra.db import transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.admission import CallContext
from a13n_service.runs.seal import LeaseExpirer
from a13n_service.runs.tables import AttemptRow, RunRow, ThreadRow, UsageRecordRow
from a13n_service.tenancy.tables import GrantRow
from a13n_stream_protocol.display import DisplayFold
from sqlalchemy import delete, select, update

pytestmark = pytest.mark.anyio


async def test_a_message_runs_to_completion(executing, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(executing, scripted_model)
    scripted_model.say("Hello there")
    submitted = await runs_kit.start_thread(executing, agent, "hi")
    run = await runs_kit.sealed(executing, submitted["run"]["id"])

    assert run["status"] == "completed" and run["output"] == "Hello there"
    assert run["usage_at_seal"]["requests"] == 1 and run["attempts"] == 1
    request = await scripted_model.request()
    assert "hi" in str(runs_kit.user_texts(request))
    listing = await runs_kit.items(executing, run["id"])
    assert listing["complete"] and runs_kit.texts(listing) == [("user", "hi"), ("assistant", "Hello there")]
    assert all(item["state"] == "completed" for item in listing["items"] if item["kind"] == "text_message")
    # Each item keeps when its first event occurred and when the event that finished it did.
    for item in listing["items"]:
        assert datetime.fromisoformat(item["started_at"]) <= datetime.fromisoformat(item["ended_at"]), item
    entry = await executing.client.get(f"{executing.api}/threads/{run['thread_id']}/inbox")
    assert [item["status"] for item in entry.json()["items"]] == ["consumed"]


async def test_a_client_tool_waits_and_resume_answers_it(executing, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    lookup = {
        "name": "lookup",
        "description": "Look a value up",
        "parameters_json_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
    }
    agent = await runs_kit.create_agent(executing, scripted_model, client_tools=[lookup])
    scripted_model.call("lookup", {"q": "answer"}, call_id="call_lookup")
    submitted = await runs_kit.start_thread(executing, agent, "look it up")
    waiting = await runs_kit.sealed(executing, submitted["run"]["id"])
    assert waiting["status"] == "waiting" and waiting["wait_reason"] == "call"
    assert waiting["pending"]["calls"][0] | {"presentation": None} == {
        "tool_call_id": "call_lookup",
        "tool_name": "lookup",
        "arguments": {"q": "answer"},
        "presentation": None,
    }

    scripted_model.say("It is 42")
    answer = {"approvals": {}, "calls": {"call_lookup": {"status": "returned", "value": {"value": 42}}}}
    resumed = await executing.client.post(
        f"{executing.api}/runs/{waiting['id']}/resume", json=answer, headers={"idempotency-key": "resume-1"}
    )
    assert resumed.status_code == 201, resumed.text
    successor = await runs_kit.sealed(executing, resumed.json()["id"])
    assert successor["status"] == "completed" and successor["output"] == "It is 42"
    assert successor["parent_run_id"] == waiting["id"] and successor["trigger"] == "resume"
    await scripted_model.request()
    second = await scripted_model.request()
    tool_results = [message for message in second["messages"] if message["role"] == "tool"]
    assert tool_results and "42" in tool_results[0]["content"]


async def test_a_run_override_is_frozen_and_carried_by_its_resume(executing, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    lookup = {"name": "lookup", "description": "Look a value up", "parameters_json_schema": {"type": "object"}}
    agent = await runs_kit.create_agent(executing, scripted_model, instructions="Role: default", client_tools=[lookup])
    scripted_model.call("lookup", {}, call_id="call_lookup")
    overridden = runs_kit.message(agent, "look", options={"overrides": {"instructions": "Role: stand-in"}})
    response = await executing.client.post(f"{executing.api}/threads", json=overridden, headers=runs_kit.fresh_key())
    assert response.status_code == 201, response.text
    waiting = await runs_kit.sealed(executing, response.json()["run"]["id"])
    assert waiting["status"] == "waiting"
    # The run shows the options it froze, never the caller headers frozen with them.
    assert waiting["options"]["overrides"]["instructions"] == "Role: stand-in"
    assert "mcp_headers" not in waiting["options"]

    scripted_model.say("Done")
    answer = {"approvals": {}, "calls": {"call_lookup": {"status": "returned", "value": {"value": 42}}}}
    resumed = await executing.client.post(
        f"{executing.api}/runs/{waiting['id']}/resume", json=answer, headers={"idempotency-key": "resume-1"}
    )
    assert resumed.status_code == 201, resumed.text
    assert resumed.json()["options"] == waiting["options"]
    assert (await runs_kit.sealed(executing, resumed.json()["id"]))["status"] == "completed"
    for request in (await scripted_model.request(), await scripted_model.request()):
        assert "Role: stand-in" in json.dumps(request) and "Role: default" not in json.dumps(request)


@pytest.mark.parametrize("result", [{"response": "blue please"}, {"answers": {"Which color?": "blue"}}])
async def test_a_question_response_resumes_its_exact_call(executing, scripted_model, runs_kit, result) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(executing, scripted_model, user_questions=True)
    scripted_model.call("ask_user_question", {"questions": [runs_kit.QUESTION]}, call_id="call_ask")
    submitted = await runs_kit.start_thread(executing, agent, "pick a color")
    waiting = await runs_kit.sealed(executing, submitted["run"]["id"])
    assert waiting["wait_reason"] == "call"

    scripted_model.say("Blue it is")
    reply = await executing.client.post(
        f"{executing.api}/runs/{waiting['id']}/resume",
        json={"approvals": {}, "calls": {"call_ask": {"status": "returned", "value": result}}},
        headers=runs_kit.fresh_key(),
    )
    assert reply.status_code == 201, reply.text
    successor = await runs_kit.sealed(executing, reply.json()["id"])
    assert successor["status"] == "completed" and successor["parent_run_id"] == waiting["id"]
    assert successor["trigger"] == "resume" and successor["resumed_by_id"] == waiting["principal_id"]
    assert successor["resume"]["calls"]["call_ask"]["value"] == {"answers": {}, **result}
    # The response is durable resume data, not another inbox message.
    assert len(await runs_kit.inbox(executing, waiting["thread_id"])) == 1
    await scripted_model.request()
    second = await scripted_model.request()
    answers = [m for m in second["messages"] if m["role"] == "tool" and m["tool_call_id"] == "call_ask"]
    assert len(answers) == 1 and json.loads(answers[0]["content"]) == {"answers": {}, **result}


async def test_an_interrupt_cancels_the_model_call_in_flight(executing, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(executing, scripted_model)
    gate = asyncio.Event()
    scripted_model.say("too late", gate=gate)
    submitted = await runs_kit.start_thread(executing, agent, "write a long poem")
    await scripted_model.request()
    await runs_kit.checkpointed(executing, submitted["run"]["id"])

    interrupted = await executing.client.post(f"{executing.api}/runs/{submitted['run']['id']}/interrupt")
    assert interrupted.status_code == 200, interrupted.text
    run = await runs_kit.sealed(executing, submitted["run"]["id"])
    gate.set()
    assert run["status"] == "cancelled" and run["failure"]["code"] == "cancelled"
    listing = await runs_kit.items(executing, run["id"])
    assert listing["complete"] and runs_kit.texts(listing) == [("user", "write a long poem")]
    assert listing["resume_after"] is not None
    saved = await executing.runtime.redis.xrange(
        f"a13n:thread:{run['thread_id']}", min=listing["resume_after"], max=listing["resume_after"]
    )
    assert saved and {"event", "item"} <= saved[0][1].keys()
    assert int(saved[0][1]["sequence"]) <= int(listing["position"].split("-")[1])
    entry = await executing.client.get(f"{executing.api}/threads/{run['thread_id']}/inbox")
    # The request carried the input and its checkpoint committed before the call: it was consumed.
    assert [item["status"] for item in entry.json()["items"]] == ["consumed"]


async def test_a_crashed_attempt_recovers_from_its_checkpoint(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    submitted = await runs_kit.start_thread(service, agent, "survive a crash")
    run_id = submitted["run"]["id"]
    # The lost call never answers, so its orphaned request cannot take the recovery's turn.
    scripted_model.say("lost", gate=asyncio.Event())
    first = await runs_kit.attempt(service)
    await scripted_model.request()
    await runs_kit.checkpointed(service, run_id)
    # The worker dies while the model call is in flight, after the checkpoint before it committed.
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(AttemptRow).where(AttemptRow.run_id == run_id).values(lease_expires_at=AttemptRow.created_at)
        )
    await LeaseExpirer(service.runtime, batch=10)()
    async with transaction(service.runtime.storage) as session:
        await session.execute(update(RunRow).where(RunRow.id == run_id).values(available_at=RunRow.created_at))

    scripted_model.say("Recovered")
    await (await runs_kit.attempt(service))
    run = await runs_kit.get_run(service, run_id)
    assert run["status"] == "completed" and run["output"] == "Recovered" and run["attempts"] == 2
    attempts = (await service.client.get(f"{service.api}/runs/{run_id}/attempts")).json()["items"]
    assert [(item["status"], item["start_reason"]) for item in attempts] == [
        ("failed", "initial"),
        ("succeeded", "recovery"),
    ]
    # The restored state already holds the input, so the retried request carries it exactly once.
    retried = await scripted_model.request()
    assert str(runs_kit.user_texts(retried)).count("survive a crash") == 1
    assert runs_kit.texts(await runs_kit.items(service, run_id)) == [
        ("user", "survive a crash"),
        ("assistant", "Recovered"),
    ]


async def test_a_draining_worker_hands_the_run_off(serve, settings, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    # A handoff is not a failure: it spends no attempt, even of a run that may be attempted only once.
    once = settings.model_copy(update={"worker": settings.worker.model_copy(update={"max_attempts": 1})})
    async with serve(settings=once) as service:
        agent = await runs_kit.create_agent(service, scripted_model)
        submitted = await runs_kit.start_thread(service, agent, "hand me off")
        run_id = submitted["run"]["id"]
        gate = asyncio.Event()
        scripted_model.say("aborted", gate=gate)
        await (await runs_kit.attempt(service, handoff=True))
        gate.set()
        handed = await runs_kit.get_run(service, run_id)
        assert handed["status"] == "accepted"
        # The request was cancelled as it started; whether it reached the model does not matter.
        scripted_model.turns.clear()

        scripted_model.say("Continued")
        await (await runs_kit.attempt(service))
        run = await runs_kit.get_run(service, run_id)
        assert run["status"] == "completed" and run["output"] == "Continued" and run["attempts"] == 1, run
        attempts = (await service.client.get(f"{service.api}/runs/{run_id}/attempts")).json()["items"]
        assert [(item["status"], item["start_reason"]) for item in attempts] == [
            ("yielded", "initial"),
            ("succeeded", "handoff"),
        ]


async def test_the_thread_stream_carries_live_output_and_resumes(executing, scripted_model, listen, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(executing, scripted_model)
    gate = asyncio.Event()
    scripted_model.say("Streaming reply", gate=gate)
    headers = await runs_kit.bearer(executing)
    async with listen(executing.app) as base:
        created = await executing.client.post(
            f"{executing.api}/threads",
            json=runs_kit.message(agent, "stream this"),
            headers=runs_kit.fresh_key(),
        )
        thread_id = created.json()["thread"]["id"]
        url = f"{base}{executing.api}/threads/{thread_id}/stream"
        async with runs_kit.frames(url, headers) as stream:
            before = await runs_kit.until(stream, lambda frame: frame[0] == "boundary")
            # The input the run was offered streams before the checkpoint that consumed it.
            display = DisplayFold(created.json()["run"]["id"], attempt=1)
            for event, _, data in before:
                if event == "delta":
                    display.fold([data["event"]])
            inputs = [
                item.content.get("text")
                for item in display.items.values()
                if item.kind == "text_message" and item.content.get("role") == "user"
            ]
            assert "stream this" in inputs
            first_delta = next(entry_id for event, entry_id, _ in before if event == "delta")
            gate.set()
            after = await runs_kit.until(
                stream, lambda frame: frame[0] == "delta" and "Streaming reply" in str(frame[2])
            )
        assert all(
            data["item"] is None or data["item"]["id"].startswith("itm_")
            for event, _, data in after
            if event == "delta"
        )

        # Resuming after the first delta continues its sequence without a gap.
        async with runs_kit.frames(url, {**headers, "last-event-id": first_delta}) as resumed:
            event, _, data = await anext(resumed)
            assert event == "delta" and data["sequence"] == 2
        # A position that is not one is refused, instead of a gap the client would reconnect after forever.
        stream_path = f"{executing.api}/threads/{thread_id}/stream"
        malformed = await executing.client.get(stream_path, headers={**headers, "last-event-id": "latest"})
        assert malformed.status_code == 400, malformed.text


async def test_a_run_stops_when_its_principal_loses_authority(executing, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(executing, scripted_model)
    scripted_model.say("never sent", gate=asyncio.Event())
    run_id = (await runs_kit.start_thread(executing, agent, "revoke me"))["run"]["id"]
    await scripted_model.request()
    async with transaction(executing.runtime.storage) as session:
        await session.execute(delete(GrantRow).where(GrantRow.principal_id == executing.tenant.principal_id))
    # The API no longer admits this principal either, so the run is read from the database.
    async with asyncio.timeout(20):
        while True:
            async with transaction(executing.runtime.storage) as session:
                run = await session.get(RunRow, run_id)
                assert run is not None
                if run.status in runs_kit.SEALED:
                    break
            await asyncio.sleep(0.05)
    assert run.status == "failed" and run.failure == {
        "code": "authority_revoked",
        "message": "The run's principal can no longer run it",
    }


async def test_a_run_whose_principal_lost_authority_before_its_attempt_fails_as_revoked(
    service, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "revoke me"))["run"]["id"]
    async with transaction(service.runtime.storage) as session:
        await session.execute(delete(GrantRow).where(GrantRow.principal_id == service.tenant.principal_id))
    await (await runs_kit.attempt(service))
    async with transaction(service.runtime.storage) as session:
        run = await session.get(RunRow, run_id)
    # Planning an attempt rechecks the authority as the supervisor's renewal does, with the same outcome.
    assert run is not None and (run.status, run.failure) == (
        "failed",
        {"code": "authority_revoked", "message": "The run's principal can no longer run it"},
    )
    assert scripted_model.requests.empty()


async def _archive(service) -> None:  # type: ignore[no-untyped-def]
    workspace = await service.client.get(service.workspace)
    archived = await service.client.post(
        f"{service.workspace}/archive", headers={"if-match": workspace.headers["etag"]}
    )
    assert archived.status_code == 200, archived.text


async def test_archiving_the_workspace_stops_its_running_runs(executing, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(executing, scripted_model)
    scripted_model.say("never sent", gate=asyncio.Event())
    run_id = (await runs_kit.start_thread(executing, agent, "archive me"))["run"]["id"]
    await scripted_model.request()
    await _archive(executing)
    run = await runs_kit.sealed(executing, run_id)
    assert (run["status"], run["failure"]["code"]) == ("failed", "authority_revoked"), run


async def test_an_archived_workspace_starts_no_queued_or_accepted_run(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    gate = asyncio.Event()
    scripted_model.say("one", gate=gate)
    first = await runs_kit.start_thread(service, agent, "first")
    thread_id = first["thread"]["id"]
    queued = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "second", delivery="next_run"))
    accepted = (await runs_kit.start_thread(service, agent, "other"))["run"]
    running = await runs_kit.attempt(service)
    await scripted_model.request()
    await _archive(service)
    # Without a supervisor renewing its authority, the running attempt completes; its successor never starts.
    gate.set()
    await running
    assert (await runs_kit.get_run(service, first["run"]["id"]))["status"] == "completed"
    (entry,) = [item for item in await runs_kit.inbox(service, thread_id) if item["id"] == queued.json()["entry"]["id"]]
    assert (entry["status"], entry["failure"]["code"]) == ("failed", "disabled"), entry
    assert (await runs_kit.get_thread(service, thread_id))["last_run_id"] == first["run"]["id"]

    # A run accepted before the archive fails when its attempt is planned, before any model call.
    await (await runs_kit.attempt(service))
    run = await runs_kit.get_run(service, accepted["id"])
    assert (run["status"], run["failure"]["code"]) == ("failed", "authority_revoked"), run
    assert scripted_model.requests.empty()


class _Refusing:
    """An additional admission policy that accepts runs and refuses every paid call."""

    def __init__(self) -> None:
        self.calls: list[CallContext] = []

    async def accept(self, session, intent) -> None:  # type: ignore[no-untyped-def]
        return None

    async def proceed(self, session, call: CallContext) -> None:  # type: ignore[no-untyped-def]
        self.calls.append(call)
        raise ServiceError("rate_limited", "The budget is spent")


async def test_an_admission_refusal_prevents_the_call_and_fails_the_run(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "spend"))["run"]["id"]
    policy = _Refusing()
    runtime = replace(service.runtime, admission=policy)
    await (await runs_kit.attempt(service, runtime=runtime))
    run = await runs_kit.get_run(service, run_id)
    assert run["status"] == "failed" and run["failure"]["code"] == "rate_limited"
    # The refused request never reached the model, and the refusal saw the call's identity before dispatch.
    assert scripted_model.requests.empty()
    assert [(call.run_id, call.source) for call in policy.calls] == [(run_id, "agent")]
    assert policy.calls[0].call_id and policy.calls[0].model_id is not None
    listing = await runs_kit.items(service, run_id)
    assert listing["complete"] and listing["resume_after"] is not None
    saved = await service.runtime.redis.xrange(
        f"a13n:thread:{run['thread_id']}", min=listing["resume_after"], max=listing["resume_after"]
    )
    assert saved and {"event", "item"} <= saved[0][1].keys()
    assert int(saved[0][1]["sequence"]) <= int(listing["position"].split("-")[1])


async def test_an_inline_subagent_runs_inside_its_parents_run(executing, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.delegating(executing, scripted_model, "inline")
    scripted_model.call("delegate", {"subagent": "helper", "prompt": "compute"}, call_id="call_d", to="coordinator")
    scripted_model.say("42", to="worker")
    scripted_model.say("The helper said 42", to="coordinator")
    run = await runs_kit.sealed(
        executing, (await runs_kit.start_thread(executing, agent, "ask the helper"))["run"]["id"]
    )

    assert run["status"] == "completed" and run["output"] == "The helper said 42", run
    assert run["usage_at_seal"]["requests"] == 3
    async with transaction(executing.runtime.storage) as session:
        records = (await session.scalars(select(UsageRecordRow).where(UsageRecordRow.run_id == run["id"]))).all()
        threads = (await session.scalars(select(ThreadRow).where(ThreadRow.origin_run_id == run["id"]))).all()
    # The child's charges are the parent run's, attributed to the model serving them, under the child's own
    # Harness run; no child thread exists.
    scopes = [record for record in records if record.record["kind"] == "cursor"]
    contributions = [record for record in records if record.record["kind"] == "model"]
    assert len(scopes) == 2 and len(contributions) == 3
    assert all(record.model_id is not None for record in contributions)
    assert {record.harness_run_id for record in scopes} == {record.harness_run_id for record in contributions}
    assert len({record.harness_run_id for record in records}) == 2 and not threads


async def test_an_async_subagent_runs_as_a_child_run_and_reports_back(executing, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.delegating(executing, scripted_model, "async")
    scripted_model.call(
        "delegate", {"subagent_name": "helper", "prompt": "compute"}, call_id="call_d", to="coordinator"
    )
    scripted_model.say("Delegated", to="coordinator")
    submitted = await runs_kit.start_thread(executing, agent, "ask the helper")
    parent = await runs_kit.sealed(executing, submitted["run"]["id"])
    assert parent["status"] == "completed" and parent["output"] == "Delegated", parent

    async with transaction(executing.runtime.storage) as session:
        (child_thread,) = (
            await session.scalars(select(ThreadRow).where(ThreadRow.origin_run_id == parent["id"]))
        ).all()
    assert (child_thread.origin, child_thread.origin_tool_call_id) == ("child", "call_d")
    assert (await runs_kit.get_thread(executing, child_thread.id))["subagent"] == "helper"
    scripted_model.say("42", to="worker")
    child = await runs_kit.sealed(executing, child_thread.current_run_id)  # type: ignore[arg-type]
    assert child["status"] == "completed" and child["output"] == "42"

    # The child's result arrives in the parent thread as its own entry and starts the next parent run.
    scripted_model.say("The helper said 42", to="coordinator")
    async with asyncio.timeout(20):
        while (thread := await executing.client.get(f"{executing.api}/threads/{parent['thread_id']}")).json()[
            "last_run_id"
        ] == parent["id"]:
            await asyncio.sleep(0.05)
    delivered = await runs_kit.sealed(executing, thread.json()["last_run_id"])
    assert delivered["status"] == "completed" and delivered["output"] == "The helper said 42", delivered
    assert delivered["trigger"] == "child_result"
    # The result names the edge that delegated the child, as the request a Console shows for the run.
    assert (delivered["input"]["subagent"], delivered["input"]["child_run_id"]) == ("helper", child["id"])
    received = []
    while not scripted_model.requests.empty():
        received.append(scripted_model.requests.get_nowait())
    assert "42" in str(runs_kit.user_texts(received[-1]))


async def test_the_agent_composer_creates_an_agent_once_approved(executing, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    model = await runs_kit.create_model(executing, scripted_model)
    agents = f"{executing.api}/agents"
    prepared = await executing.client.post(f"{executing.api}/agent-composer")
    assert prepared.status_code == 200, prepared.text
    composer = prepared.json()
    assert composer["source"] == "builtin"
    builtin = (await executing.client.get(agents, params={"source": "builtin"})).json()["items"]
    assert [agent["id"] for agent in builtin] == [composer["id"]]
    again = (await executing.client.post(f"{executing.api}/agent-composer")).json()
    assert again["default_revision_id"] == composer["default_revision_id"]
    edited = await executing.client.post(
        f"{agents}/{composer['id']}/revisions",
        json={"config": {"model": model}},
        headers={"if-match": f'"{composer["id"]}:{composer["version"]}"'},
    )
    assert edited.status_code == 409 and edited.json()["error"]["details"]["reason"] == "builtin"

    async def custom() -> list[dict[str, Any]]:
        return (await executing.client.get(agents, params={"source": "custom"})).json()["items"]

    scripted_model.call("find_resources", {"kind": "model"}, call_id="call_find")
    config = {"model": model, "instructions": "Greet people."}
    scripted_model.call("create_agent", {"name": "Greeter", "config": config}, call_id="call_create")
    waiting = await runs_kit.sealed(
        executing, (await runs_kit.start_thread(executing, composer, "make a greeter"))["run"]["id"]
    )
    assert waiting["status"] == "waiting" and waiting["wait_reason"] == "approval", waiting
    assert await custom() == []

    scripted_model.say("Created the greeter")
    approve = {"approvals": {"call_create": {"action": "approve"}}, "calls": {}}
    resumed = await executing.client.post(
        f"{executing.api}/runs/{waiting['id']}/resume", json=approve, headers={"idempotency-key": "approve-1"}
    )
    assert resumed.status_code == 201, resumed.text
    assert (await runs_kit.sealed(executing, resumed.json()["id"]))["status"] == "completed"
    [greeter] = await custom()
    assert greeter["name"] == "Greeter"
    revision = f"{agents}/{greeter['id']}/revisions/{greeter['default_revision_id']}"
    # Created with the authority of the user whose message started the run.
    assert (await executing.client.get(revision)).json()["created_by_id"] == executing.tenant.principal_id
    requests = [await scripted_model.request() for _ in range(3)]
    found = [message for message in requests[1]["messages"] if message["role"] == "tool"]
    assert model in found[0]["content"]
    created = [message for message in requests[2]["messages"] if message["role"] == "tool"][-1]
    assert json.loads(created["content"]) == {
        "agent_id": greeter["id"],
        "default_revision_id": greeter["default_revision_id"],
    }


async def test_the_agent_composer_reads_where_its_arguments_do_not_fit(executing, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.create_model(executing, scripted_model)
    composer = (await executing.client.post(f"{executing.api}/agent-composer")).json()
    config = {"model": "Bad Model", "instructions": "Greet people."}
    scripted_model.call("create_agent", {"name": "Greeter", "config": config}, call_id="call_create")
    waiting = await runs_kit.sealed(
        executing, (await runs_kit.start_thread(executing, composer, "make a greeter"))["run"]["id"]
    )
    scripted_model.say("That model does not fit")
    approve = {"approvals": {"call_create": {"action": "approve"}}, "calls": {}}
    resumed = await executing.client.post(
        f"{executing.api}/runs/{waiting['id']}/resume", json=approve, headers={"idempotency-key": "approve-1"}
    )
    assert (await runs_kit.sealed(executing, resumed.json()["id"]))["status"] == "completed"
    requests = [await scripted_model.request() for _ in range(2)]
    [failure] = [
        json.loads(message["content"])["error"] for message in requests[1]["messages"] if message["role"] == "tool"
    ]
    # The location and the schema's own message, never the submitted value.
    assert failure.startswith("config.model: String should match pattern") and "Bad Model" not in failure, failure


async def test_the_agent_composer_reads_the_revision_it_starts_from(executing, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    """Configuring from an older version names a revision other than the default, which the composer reads."""
    model = await runs_kit.create_model(executing, scripted_model)
    target = await runs_kit.add_agent(executing, "research", model, instructions="Version one.")
    newer = await executing.client.post(
        f"{executing.api}/agents/{target['id']}/revisions",
        json={"config": {"model": model, "instructions": "Version two."}},
        headers=runs_kit.if_match(target),
    )
    assert newer.status_code == 201, newer.text
    composer = (await executing.client.post(f"{executing.api}/agent-composer")).json()

    older = {"kind": "agent", "reference": target["id"], "revision_id": target["default_revision_id"]}
    scripted_model.call("read_resource", older, call_id="call_read")
    scripted_model.call("read_resource", {**older, "kind": "model", "reference": model}, call_id="call_model")
    scripted_model.say("Read it")
    started = await runs_kit.start_thread(executing, composer, "change research from version 1")
    assert (await runs_kit.sealed(executing, started["run"]["id"]))["status"] == "completed"
    requests = [await scripted_model.request() for _ in range(3)]
    [read] = [message for message in requests[1]["messages"] if message["role"] == "tool"]
    assert "Version one." in read["content"] and "Version two." not in read["content"]
    refused = [message for message in requests[2]["messages"] if message["role"] == "tool"][-1]
    assert "only an agent is read at a revision" in refused["content"]


async def test_the_agent_composer_follows_the_usable_models(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    prepare = f"{service.api}/agent-composer"
    refused = await service.client.post(prepare)
    assert refused.status_code == 409 and refused.json()["error"]["details"]["reason"] == "model_required"
    first = await runs_kit.create_model(service, scripted_model)
    composer = (await service.client.post(prepare)).json()
    revision = f"{service.api}/agents/{composer['id']}/revisions/{composer['default_revision_id']}"
    assert (await service.client.get(revision)).json()["config"]["model"] == first

    # Disabling the model it runs on moves it to another usable model with a new revision.
    second = await service.client.post(
        f"{service.api}/models",
        json={
            "provider_id": (await service.client.get(f"{service.api}/models/{first}")).json()["provider_id"],
            "key": "backup",
            "name": "Backup",
            "config": {"model_name": "vendor/claude-sonnet-5", "model_api": "openai.chat_completions"},
        },
    )
    assert second.status_code == 201, second.text
    assert (await service.client.post(prepare)).json()["default_revision_id"] == composer["default_revision_id"]
    model = (await service.client.get(f"{service.api}/models/{first}")).json()
    disabled = await service.client.patch(
        f"{service.api}/models/{first}",
        json={"enabled": False},
        headers={"if-match": f'"{model["key"]}:{model["version"]}"'},
    )
    assert disabled.status_code == 200, disabled.text
    moved = (await service.client.post(prepare)).json()
    assert moved["default_revision_id"] != composer["default_revision_id"]
    revision = f"{service.api}/agents/{moved['id']}/revisions/{moved['default_revision_id']}"
    assert (await service.client.get(revision)).json()["config"]["model"] == second.json()["key"] == "backup"
