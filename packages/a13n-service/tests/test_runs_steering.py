"""Steering: pending messages joining the active run at its next safe boundary."""

import asyncio
from dataclasses import replace

import pytest
from a13n_service.infra.db import transaction
from a13n_service.runs.tables import InboxEntryRow
from sqlalchemy import update

pytestmark = pytest.mark.anyio


async def _tool_turn(service, scripted_model, runs_kit) -> tuple[dict, str, asyncio.Event]:  # type: ignore[no-untyped-def]
    """A thread whose run first calls a tool, once `gate` opens, and then ends; steers join after the tool."""
    agent = await runs_kit.create_agent(service, scripted_model, **runs_kit.LOOKUP)
    gate = asyncio.Event()
    runs_kit.lookup(scripted_model, call_id="call_find", gate=gate)
    scripted_model.say("Done")
    thread_id = (await runs_kit.start_thread(service, agent, "look around"))["thread"]["id"]
    return agent, thread_id, gate


async def test_a_steer_joins_past_a_larger_entry_for_a_later_run(skill_service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    service = skill_service
    agent, thread_id, gate = await _tool_turn(service, scripted_model, runs_kit)
    # One delivery batch holds 1 KiB; the entry queued for a later run alone is larger.
    worker = service.runtime.settings.worker.model_copy(update={"delivery_bytes": 1024})
    runtime = replace(service.runtime, settings=service.runtime.settings.model_copy(update={"worker": worker}))
    running = await runs_kit.attempt(service, runtime=runtime)
    await scripted_model.request()
    later = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "x" * 2048, delivery="next_run"))
    steer = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "also count the agents"))
    gate.set()
    await running

    # Pending when the run reached its tool boundary, the steer joins the request that follows the tool.
    (following,) = [scripted_model.requests.get_nowait() for _ in range(scripted_model.requests.qsize())]
    assert "also count the agents" in str(runs_kit.user_texts(following))
    statuses = {entry["id"]: entry["status"] for entry in await runs_kit.inbox(service, thread_id)}
    assert statuses[steer.json()["entry"]["id"]] == "consumed"
    matched = await service.client.get(f"{service.api}/runs", params={"text_query": "also count"})
    assert [run["thread_id"] for run in matched.json()["items"]] == [thread_id]

    # The larger entry started the thread's next run.
    assert statuses[later.json()["entry"]["id"]] == "assigned"


async def test_a_steer_that_cannot_be_read_fails_alone(skill_service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    service = skill_service
    agent, thread_id, gate = await _tool_turn(service, scripted_model, runs_kit)
    running = await runs_kit.attempt(service)
    await scripted_model.request()
    unreadable = {
        "agent_id": agent["id"],
        "payload": {"content": [{"type": "url", "url": f"{scripted_model.url}/gone"}]},
    }
    rejected = (await runs_kit.submit(service, thread_id, unreadable)).json()["entry"]
    gate.set()
    await running

    thread = await runs_kit.get_thread(service, thread_id)
    run = await runs_kit.get_run(service, thread["last_run_id"])
    assert run["status"] == "completed" and run["output"] == "Done", run
    (entry,) = [entry for entry in await runs_kit.inbox(service, thread_id) if entry["id"] == rejected["id"]]
    assert entry["status"] == "failed" and entry["failure"]["code"] == "invalid_argument", entry


async def test_a_steer_a_recovered_attempt_cannot_read_fails_alone(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    scripted_model.say("Done")
    submitted = await runs_kit.start_thread(service, agent, "hello")
    thread_id, run_id = submitted["thread"]["id"], submitted["run"]["id"]
    gone = {"agent_id": agent["id"], "payload": {"content": [{"type": "url", "url": f"{scripted_model.url}/gone"}]}}
    steer = (await runs_kit.submit(service, thread_id, gone)).json()["entry"]
    # An earlier attempt assigned the steer at a boundary and ended before a checkpoint incorporated it.
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(InboxEntryRow)
            .where(InboxEntryRow.id == steer["id"])
            .values(status="assigned", assigned_run_id=run_id)
        )
    await (await runs_kit.attempt(service))

    run = await runs_kit.get_run(service, run_id)
    assert (run["status"], run["output"]) == ("completed", "Done"), run
    assert "hello" in str(runs_kit.user_texts(await scripted_model.request()))
    (entry,) = [entry for entry in await runs_kit.inbox(service, thread_id) if entry["id"] == steer["id"]]
    assert (entry["status"], entry["failure"]["code"]) == ("failed", "invalid_argument"), entry


async def test_a_steer_joins_with_the_default_options_or_the_runs_own(skill_service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    """Acceptance freezes a run's options with their pins resolved; a steer names them as submitted, or none."""
    service = skill_service
    agent = await runs_kit.delegating(service, scripted_model, "inline", **runs_kit.LOOKUP)
    gate = asyncio.Event()
    for _ in range(3):
        scripted_model.say("Done", gate=gate, to="Role: coordinator")
    options = {"labels": {"team": "a"}, "overrides": {"subagents": {"helper": {"description": "Overridden helper"}}}}
    submitted = await runs_kit.start_thread(service, agent, "look around", options=options)
    thread_id, run_id = submitted["thread"]["id"], submitted["run"]["id"]
    running = await runs_kit.attempt(service)
    await scripted_model.request()
    # A client such as the Console steers with no options; another names the run's options as submitted.
    plain = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "also count"))
    assert "also count" in str(runs_kit.user_texts(await scripted_model.request()))
    same = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "and list", options=options))
    assert "and list" in str(runs_kit.user_texts(await scripted_model.request()))
    other = await runs_kit.submit(
        service, thread_id, runs_kit.message(agent, "with a label", options={**options, "labels": {"team": "b"}})
    )
    gate.set()
    await running

    entries = {entry["id"]: entry for entry in await runs_kit.inbox(service, thread_id)}
    for joining in (plain, same):
        steered = entries[joining.json()["entry"]["id"]]
        assert (steered["status"], steered["assigned_run_id"]) == ("consumed", run_id), steered
    queued = entries[other.json()["entry"]["id"]]
    assert queued["assigned_run_id"] != run_id and queued["options"]["labels"] == {"team": "b"}, queued
