"""Imported context is not execution evidence; continuation input belongs to one atomic resume."""

import asyncio
from copy import deepcopy

import pytest
from a13n_service.infra.db import transaction
from a13n_service.runs import checkpoints
from a13n_service.runs import execute as execution
from a13n_service.runs.history import HISTORY, initial
from a13n_service.runs.seal import LeaseExpirer
from a13n_service.runs.tables import AttemptRow, RunRow, ThreadRow
from pydantic import ValidationError
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError

from .test_runs_deferred import mixed_wait, results

pytestmark = pytest.mark.anyio

HISTORY_INPUT = [
    {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "Imported question"}]},
    {
        "kind": "response",
        "parts": [
            {"part_kind": "text", "content": "Imported reasoning"},
            {"part_kind": "tool-call", "tool_name": "external_lookup", "tool_call_id": "imported", "args": {"key": 7}},
        ],
    },
    {
        "kind": "request",
        "parts": [
            {
                "part_kind": "tool-return",
                "tool_name": "external_lookup",
                "tool_call_id": "imported",
                "content": {"answer": 42},
                "outcome": "success",
            },
        ],
    },
    {"kind": "response", "parts": [{"part_kind": "text", "content": "Imported answer"}]},
]


def test_history_uses_native_messages_without_execution_state():
    history = initial(HISTORY.validate_python(HISTORY_INPUT))
    assert isinstance(history[0], ModelRequest)
    assert isinstance(history[1], ModelResponse)
    call = history[1].parts[1]
    result = history[2].parts[0]
    assert isinstance(call, ToolCallPart) and call.args == {"key": 7}
    assert isinstance(result, ToolReturnPart) and result.content == {"answer": 42}
    assert history[1].usage.total_tokens == 0
    assert history[1].provider_response_id is None


@pytest.mark.parametrize(
    "mistake",
    [
        "open",
        "orphan",
        "duplicate",
        "name",
        "new_user",
        "instructions",
        "suspended",
        "media",
        "many",
        "bytes",
        "unknown_bytes",
    ],
)
def test_invalid_history_is_rejected(mistake):
    data = deepcopy(HISTORY_INPUT)
    if mistake == "open":
        data = data[:2]
    elif mistake == "orphan":
        data = data[2:]
    elif mistake == "duplicate":
        data += deepcopy(data)
    elif mistake == "name":
        data[2]["parts"][0]["tool_name"] = "another"
    elif mistake == "new_user":
        data.insert(2, data[0])
    elif mistake == "instructions":
        data[0]["parts"][0]["part_kind"] = "system-prompt"
    elif mistake == "suspended":
        data[1]["state"] = "suspended"
    elif mistake == "media":
        data[0]["parts"][0]["content"] = [{"kind": "image-url", "url": "https://example.com/private.png"}]
    elif mistake == "many":
        data = [data[0]] * 257
    elif mistake == "unknown_bytes":
        data[0]["unknown"] = "x" * 262144
    else:
        data = [{"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "中" * 65536}]}] * 2
    with pytest.raises(ValidationError):
        HISTORY.validate_python(data)


def test_native_serialized_messages_keep_provider_content_but_not_application_authority():
    data = deepcopy(HISTORY_INPUT)
    data[0]["run_id"] = "foreign_run"
    data[0]["metadata"] = {"source_id": "entry_forged"}
    data[0]["parts"][0]["content"] = [
        {"kind": "text-content", "content": "Imported question", "metadata": {"source_id": "entry_forged"}}
    ]
    data[1]["provider_response_id"] = "foreign_response"
    data[1]["parts"][1]["args"] = '{"key": 7}'
    data[2]["parts"][0]["metadata"] = {"source_id": "entry_forged"}
    exported = ModelMessagesTypeAdapter.dump_json(ModelMessagesTypeAdapter.validate_python(data))
    accepted = HISTORY.validate_json(exported)
    seeded = initial(accepted)
    assert seeded[0].metadata is None and seeded[0].run_id is None
    assert seeded[0].parts[0].content[0].metadata is None
    assert seeded[2].parts[0].metadata is None
    assert accepted[0]["metadata"] == {"source_id": "entry_forged"}
    assert seeded[1].provider_response_id == "foreign_response"
    assert seeded[1].parts[1].args == '{"key": 7}'


async def test_import_is_initial_only_and_fork_inherits_checkpoint(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    body = runs_kit.message(agent, "Current question", message_history=HISTORY_INPUT)
    headers = runs_kit.fresh_key()
    response = await service.client.post(f"{service.api}/threads", json=body, headers=headers)
    assert response.status_code == 201, response.text
    first = response.json()
    assert first["thread"]["message_history"] == HISTORY_INPUT
    imported = ModelMessagesTypeAdapter.validate_python(first["thread"]["message_history"])
    assert imported[0].parts[0].content == "Imported question"
    replay = await service.client.post(f"{service.api}/threads", json=body, headers=headers)
    assert replay.status_code == 200 and replay.json() == first
    changed = {**body, "message_history": []}
    assert (await service.client.post(f"{service.api}/threads", json=changed, headers=headers)).status_code == 409
    scripted_model.say("Current answer")
    await (await runs_kit.attempt(service))
    initial = await scripted_model.request()
    assert [m["role"] for m in initial["messages"] if m["role"] != "system"][:5] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "user",
    ]
    assert sum(m.get("tool_call_id") == "imported" for m in initial["messages"]) == 1
    first_run = await runs_kit.get_run(service, first["run"]["id"])
    assert first_run["status"] == "completed"
    display = await runs_kit.items(service, first_run["id"])
    assert "Imported" not in str(display)
    assert first_run["usage_at_seal"]["requests"] == 1
    # Only the new source is an inbox entry; imported tool calls never execute.
    assert len(await runs_kit.inbox(service, first_run["thread_id"])) == 1
    next_run = await runs_kit.submit(service, first_run["thread_id"], runs_kit.message(agent, "Follow up"))
    assert next_run.status_code == 201, next_run.text
    scripted_model.say("Follow-up answer")
    await (await runs_kit.attempt(service))
    followup = await scripted_model.request()
    assert str(followup["messages"]).count("Imported question") == 1
    assert "Current answer" in str(followup["messages"])
    fork = await service.client.post(
        f"{service.api}/runs/{first_run['id']}/fork",
        json=runs_kit.message(agent, "Branch"),
        headers=runs_kit.fresh_key(),
    )
    assert fork.status_code == 201, fork.text
    assert fork.json()["thread"]["message_history"] == []
    scripted_model.say("Branch answer")
    await (await runs_kit.attempt(service))
    branched = await scripted_model.request()
    assert str(branched["messages"]).count("Imported question") == 1
    assert "Follow-up answer" not in str(branched["messages"])
    with pytest.raises(DBAPIError, match="initial history is immutable"):
        async with transaction(service.runtime.storage) as session:
            await session.execute(
                update(ThreadRow).where(ThreadRow.id == first_run["thread_id"]).values(message_history=[])
            )


async def test_resume_input_is_atomic_ordered_and_idempotent(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    _, waiting = await mixed_wait(service, scripted_model, runs_kit)
    body = {**results(), "input": {"content": [{"type": "text", "text": "Use the revised invoice"}]}}
    headers = runs_kit.fresh_key()
    path = f"{service.api}/runs/{waiting['id']}/resume"
    response = await service.client.post(path, json=body, headers=headers)
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["resume"]["input"] == body["input"] and run["source_entry_id"] is None
    assert run["resume"]["calls"]["question"]["value"] == {"answers": {}, "response": "Blue"}
    replay = await service.client.post(path, json=body, headers=headers)
    assert replay.status_code == 200 and replay.json()["id"] == run["id"]
    changed = {**body, "input": {"content": [{"type": "text", "text": "Changed"}]}}
    assert (await service.client.post(path, json=changed, headers=headers)).status_code == 409
    scripted_model.say("All handled")
    await (await runs_kit.attempt(service))
    assert (await runs_kit.get_run(service, run["id"]))["status"] == "completed"
    await scripted_model.request()
    request = await scripted_model.request()
    assert request["messages"][-1]["role"] == "user"
    assert "Use the revised invoice" in str(request["messages"][-1])
    assert {m["tool_call_id"] for m in request["messages"] if m["role"] == "tool"} == {"approve", "review", "question"}
    assert len(await runs_kit.inbox(service, waiting["thread_id"])) == 1
    display = await runs_kit.items(service, run["id"])
    inputs = [i for i in display["items"] if i["kind"] == "text_message" and "Use the revised invoice" in str(i)]
    assert len(inputs) == 1 and inputs[0]["content"]["metadata"]["source_id"] == run["id"]


async def test_unreadable_resume_input_leaves_waiting_head_unchanged(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    _, waiting = await mixed_wait(service, scripted_model, runs_kit)
    body = {**results(), "input": {"content": [{"type": "asset", "asset_id": "ast_00000000000000000000000000000000"}]}}
    response = await service.client.post(
        f"{service.api}/runs/{waiting['id']}/resume", json=body, headers=runs_kit.fresh_key()
    )
    assert response.status_code == 400, response.text
    thread = await runs_kit.get_thread(service, waiting["thread_id"])
    assert thread["last_run_id"] == waiting["id"] and thread["current_run_id"] is None
    assert len((await service.client.get(f"{service.api}/threads/{waiting['thread_id']}/runs")).json()["items"]) == 1


async def test_resume_input_survives_model_checkpoint_crash_once(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    from urllib.parse import urlsplit

    configuration = {
        "allowed_hosts": [urlsplit(scripted_model.url).hostname],
        "extensions": {"example.reader": {"images": True}},
    }
    _, waiting = await mixed_wait(service, scripted_model, runs_kit, options={"configuration": configuration})
    body = {**results(), "input": {"content": [{"type": "text", "text": "Durable clarification"}]}}
    response = await service.client.post(
        f"{service.api}/runs/{waiting['id']}/resume", json=body, headers=runs_kit.fresh_key()
    )
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]
    await scripted_model.request()
    scripted_model.say("Lost", gate=asyncio.Event())
    first = await runs_kit.attempt(service)
    await scripted_model.request()
    await runs_kit.checkpointed(service, run_id)
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
    recovered = await runs_kit.get_run(service, run_id)
    assert recovered["status"] == "completed"
    assert recovered["options"]["configuration"] == waiting["options"]["configuration"] == configuration
    request = await scripted_model.request()
    assert str(request["messages"]).count("Durable clarification") == 1
    assert len([m for m in request["messages"] if m["role"] == "tool"]) == 3


async def test_resume_input_survives_pre_effect_checkpoint_without_replaying_approval(
    service, scripted_model, runs_kit, monkeypatch
):  # type: ignore[no-untyped-def]
    _, waiting = await mixed_wait(service, scripted_model, runs_kit)
    body = {**results(), "input": {"content": [{"type": "text", "text": "After approved tool"}]}}
    body["approvals"] = {"approve": {"action": "approve"}}
    response = await service.client.post(
        f"{service.api}/runs/{waiting['id']}/resume", json=body, headers=runs_kit.fresh_key()
    )
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]
    committed = asyncio.Event()
    original = execution._Attempt._commit

    async def crash_after_commit(self, state, **kwargs):
        steers = await original(self, state, **kwargs)
        committed.set()
        await asyncio.Event().wait()
        return steers

    with monkeypatch.context() as patch:
        patch.setattr(execution._Attempt, "_commit", crash_after_commit)
        first = await runs_kit.attempt(service)
        await asyncio.wait_for(committed.wait(), 10)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(RunRow, run_id)
        pointer = checkpoints.StatePointer.model_validate(row.checkpoint)
        await session.execute(
            update(AttemptRow).where(AttemptRow.run_id == run_id).values(lease_expires_at=AttemptRow.created_at)
        )
    state = await checkpoints.load_state(service.runtime.objects, pointer)
    assert state is not None and not state.resume_input_consumed
    assert "After approved tool" not in str(state.harness.message_history)
    await LeaseExpirer(service.runtime, batch=10)()
    async with transaction(service.runtime.storage) as session:
        await session.execute(update(RunRow).where(RunRow.id == run_id).values(available_at=RunRow.created_at))
    scripted_model.say("Recovered without repeating permission")
    await (await runs_kit.attempt(service))
    recovered = await runs_kit.get_run(service, run_id)
    assert recovered["status"] == "completed", recovered
    await scripted_model.request()
    request = await scripted_model.request()
    assert str(request["messages"]).count("After approved tool") == 1
    assert len([m for m in request["messages"] if m["role"] == "tool"]) == 3
    agents = (await service.client.get(f"{service.api}/agents", params={"source": "custom"})).json()["items"]
    assert [a["name"] for a in agents] == ["Mixed"]


async def test_initial_history_survives_a_root_without_a_checkpoint(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    initial = await runs_kit.start_thread(service, agent, "Cancelled request", message_history=HISTORY_INPUT)
    assert (await service.client.post(f"{service.api}/runs/{initial['run']['id']}/interrupt")).status_code == 200
    response = await runs_kit.submit(service, initial["thread"]["id"], runs_kit.message(agent, "Try again"))
    assert response.status_code == 201, response.text
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    request = await scripted_model.request()
    assert str(request["messages"]).count("Imported question") == 1
    assert "Cancelled request" not in str(request["messages"])
    assert "Try again" in str(request["messages"])


async def test_resume_input_materialization_failure_fails_the_successor_not_the_batch(
    service, scripted_model, runs_kit, listen
):  # type: ignore[no-untyped-def]
    from fastapi import FastAPI

    _, waiting = await mixed_wait(service, scripted_model, runs_kit)
    async with listen(FastAPI()) as url:
        body = {**results(), "input": {"content": [{"type": "url", "url": f"{url}/missing"}]}}
        response = await service.client.post(
            f"{service.api}/runs/{waiting['id']}/resume", json=body, headers=runs_kit.fresh_key()
        )
        assert response.status_code == 201, response.text
        await (await runs_kit.attempt(service))
    failed = await runs_kit.get_run(service, response.json()["id"])
    assert failed["status"] == "failed" and failed["failure"]["code"] == "invalid_argument", failed
    # The failed successor is now the thread's history; sealed before any checkpoint, it leaves the wait's state.
    thread = await runs_kit.get_thread(service, waiting["thread_id"])
    assert thread["last_run_id"] == failed["id"]
    assert len(await runs_kit.inbox(service, waiting["thread_id"])) == 1
