"""Mutable targets retain actual delivery evidence and complete only their own message execution."""

from unittest.mock import AsyncMock

import pytest
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs import accept
from a13n_service.runs.schedules import completion, dispatch, observation
from a13n_service.runs.schedules.tables import OccurrenceRow, ScheduleRow
from a13n_service.runs.session_state import Scope
from a13n_service.runs.tables import InboxEntryRow, RunRow, SessionRow, ThreadRow
from sqlalchemy import func, select

from .test_schedule_dispatch import observed_execution, schedule_for, trigger

pytestmark = pytest.mark.anyio


async def target(service, schedule, runs_kit, value):  # type: ignore[no-untyped-def]
    response = await service.client.patch(
        f"{service.api}/schedules/{schedule['id']}", json={"target": value}, headers=runs_kit.if_match(schedule)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def item(service, schedule, occurrence):  # type: ignore[no-untyped-def]
    response = await service.client.get(f"{service.api}/schedules/{schedule['id']}/occurrences/{occurrence['id']}")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("mode", ["default", "session", "thread", "create_thread"])
async def test_create_targets_replay_and_memory(service, scripted_model, runs_kit, mode):  # type: ignore[no-untyped-def]
    schedule, agent = await schedule_for(service, scripted_model, runs_kit)
    existing = await runs_kit.start_thread(service, agent, "Existing conversation")
    session_id = existing["thread"]["session_id"]
    async with transaction(service.runtime.storage) as session:
        owner = await session.get_one(SessionRow, session_id)
        owner.memory = {"enabled": True, "spaces": []}
    body = {
        "name": "Target test",
        "schedule": schedule["schedule"],
        "invocation": schedule["invocation"],
    }
    if mode != "default":
        body["target"] = {
            "mode": "new_thread" if mode == "session" else "thread",
            "session_id": session_id,
            **({"thread_id": existing["thread"]["id"]} if mode == "thread" else {}),
        }
    key = runs_kit.fresh_key()
    created = await service.client.post(f"{service.api}/schedules", json=body, headers=key)
    assert created.status_code == 201, created.text
    saved = created.json()
    replay = await service.client.post(f"{service.api}/schedules", json=body, headers=key)
    assert replay.status_code == 200 and replay.json() == saved
    async with short_session(service.runtime.storage) as session:
        assert await session.scalar(select(func.count()).select_from(OccurrenceRow)) == 0
        assert await session.scalar(select(func.count()).select_from(RunRow)) == 1
        assert (await session.get_one(SessionRow, session_id)).memory["enabled"]
        if mode == "default":
            assert not (await session.get_one(SessionRow, saved["session_id"])).memory["enabled"]
        else:
            assert saved["session_id"] == session_id
        if mode == "create_thread":
            empty = await session.get_one(ThreadRow, saved["target"]["thread_id"])
            assert empty.current_run_id is None and empty.last_run_id is None
    occurrence = await trigger(service, saved, runs_kit)
    await dispatch.process(service.runtime, saved["id"])
    sent = await item(service, saved, occurrence)
    assert sent["status"] == "submitted" and sent["session_id"] == saved["session_id"]
    if saved["target"]["thread_id"]:
        assert sent["thread_id"] == saved["target"]["thread_id"]


async def test_fixed_thread_repeated_completion_and_unrelated_runs(service, scripted_model, runs_kit, monkeypatch):  # type: ignore[no-untyped-def]
    schedule, agent = await schedule_for(service, scripted_model, runs_kit)
    schedule = await target(service, schedule, runs_kit, {"mode": "thread", "session_id": schedule["session_id"]})
    thread_id = schedule["target"]["thread_id"]
    entries = []
    for index in range(2):
        occurrence = await trigger(service, schedule, runs_kit, key=f"trigger-{index}")
        await dispatch.process(service.runtime, schedule["id"])
        sent = await item(service, schedule, occurrence)
        entries.append(sent["entry_id"])
        assert sent["thread_id"] == thread_id and sent["status"] == "submitted"
        assert (await observed_execution(service, schedule, occurrence["id"]))["has_unfinished_work"]
        scripted_model.say(f"Report {index}")
        await (await runs_kit.attempt(service))
        done = await item(service, schedule, occurrence)
        assert done["status"] == "completed" and done["execution_outcome"] == "succeeded"
        async with short_session(service.runtime.storage) as session:
            run = await session.get_one(RunRow, done["completion_run_id"])
            assert run.occurrence_trigger_entry_id == sent["entry_id"]
    assert entries[0] != entries[1]
    ordinary = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "Manual follow-up"))
    assert ordinary.status_code == 201, ordinary.text
    assert ordinary.json()["run"]["schedule_origin"] is None
    complete = AsyncMock(wraps=completion._complete)
    monkeypatch.setattr(completion, "_complete", complete)
    scripted_model.say("Manual response")
    await (await runs_kit.attempt(service))
    complete.assert_not_called()


async def test_target_change_keeps_pending_and_routes_actual_history(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    schedule, _ = await schedule_for(service, scripted_model, runs_kit)
    old_session = schedule["session_id"]
    first = await trigger(service, schedule, runs_kit)
    await dispatch.process(service.runtime, schedule["id"])
    scripted_model.say("First report")
    await (await runs_kit.attempt(service))
    first_sent = await item(service, schedule, first)
    second = await trigger(service, schedule, runs_kit, key="second")
    schedule = await target(service, schedule, runs_kit, {"mode": "thread"})
    new_session = schedule["session_id"]
    assert old_session != new_session
    assert (await item(service, schedule, second))["status"] == "pending"
    await dispatch.process(service.runtime, schedule["id"])
    second_sent = await item(service, schedule, second)
    assert second_sent["session_id"] == new_session
    assert second_sent["thread_id"] == schedule["target"]["thread_id"]
    assert (await item(service, schedule, first))["thread_id"] == first_sent["thread_id"]
    scopes = [Scope(schedule["workspace_id"], identity) for identity in (old_session, new_session)]
    async with short_session(service.runtime.storage) as session:
        snapshots = await observation.snapshots(session, scopes)
    assert snapshots[scopes[0]].schedules == []
    assert [entry.occurrence.id for entry in snapshots[scopes[0]].occurrences] == [first["id"]]
    assert [entry.occurrence.id for entry in snapshots[scopes[1]].occurrences] == [second["id"]]
    assert len(snapshots[scopes[1]].schedules) == 1


async def test_saved_session_target_survives_edit_without_cancelling_pending(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    schedule, _ = await schedule_for(service, scripted_model, runs_kit)
    occurrence = await trigger(service, schedule, runs_kit)
    old_session = schedule["session_id"]
    schedule = await target(service, schedule, runs_kit, {"mode": "new_thread"})
    assert schedule["session_id"] != old_session
    await dispatch.process(service.runtime, schedule["id"])
    assert (await item(service, schedule, occurrence))["session_id"] == old_session


async def test_fixed_target_rollback_retry_and_queued_acceptance(service, scripted_model, runs_kit, monkeypatch):  # type: ignore[no-untyped-def]
    schedule, agent = await schedule_for(service, scripted_model, runs_kit)
    existing = await runs_kit.start_thread(service, agent, "Active manual work")
    thread_id = existing["thread"]["id"]
    schedule = await target(service, schedule, runs_kit, {"mode": "thread", "thread_id": thread_id})
    occurrence = await trigger(service, schedule, runs_kit)
    with monkeypatch.context() as patch:
        patch.setattr(dispatch, "accept", AsyncMock(side_effect=ServiceError("unavailable", "Retry")))
        await dispatch.process(service.runtime, schedule["id"])
    async with transaction(service.runtime.storage) as session:
        stored = await session.get_one(OccurrenceRow, occurrence["id"])
        assert stored.status == "pending" and stored.entry_id is None
        stored.next_attempt_at = None
        assert await session.scalar(select(func.count()).select_from(InboxEntryRow)) == 1
    await dispatch.process(service.runtime, schedule["id"])
    await dispatch.process(service.runtime, schedule["id"])
    sent = await item(service, schedule, occurrence)
    async with short_session(service.runtime.storage) as session:
        entry = await session.get_one(InboxEntryRow, sent["entry_id"])
        assert entry.status == "pending" and entry.delivery == "next_run"
        assert entry.schedule_occurrence_id == occurrence["id"]
        assert await session.scalar(select(func.count()).select_from(InboxEntryRow)) == 2
    scripted_model.say("Manual finished")
    await (await runs_kit.attempt(service))
    assert (await item(service, schedule, occurrence))["status"] == "submitted"
    await accept.advance(service.runtime, thread_id)
    scripted_model.say("Scheduled response")
    await (await runs_kit.attempt(service))
    assert (await item(service, schedule, occurrence))["status"] == "completed"


async def test_target_validation_and_multiple_schedules_in_session(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    schedule, agent = await schedule_for(service, scripted_model, runs_kit)
    existing = await runs_kit.start_thread(service, agent, "Existing")
    bad = await service.client.patch(
        f"{service.api}/schedules/{schedule['id']}",
        json={
            "target": {"mode": "thread", "session_id": schedule["session_id"], "thread_id": existing["thread"]["id"]}
        },
        headers=runs_kit.if_match(schedule),
    )
    assert bad.status_code == 400, bad.text
    other = await service.client.post(
        f"{service.api}/schedules",
        headers=runs_kit.fresh_key(),
        json={
            "name": "Second",
            "schedule": schedule["schedule"],
            "invocation": schedule["invocation"],
            "target": {"mode": "new_thread", "session_id": schedule["session_id"]},
        },
    )
    assert other.status_code == 201, other.text
    scope = Scope(schedule["workspace_id"], schedule["session_id"])
    async with short_session(service.runtime.storage) as session:
        snapshot = (await observation.snapshots(session, [scope]))[scope]
        assert {entry.schedule_id for entry in snapshot.schedules} == {schedule["id"], other.json()["id"]}
        stored = await session.get_one(ScheduleRow, schedule["id"])
        assert stored.thread_id is None
