"""Schedule notices share transaction, filtering and completion boundaries with their recorded facts."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.outbox import OutboxRow
from a13n_service.resources.subscriptions.schemas import LIFECYCLE_KINDS
from a13n_service.runs import seal
from a13n_service.runs.schedules import dispatch
from a13n_service.runs.schedules.sweep import plan_next
from a13n_service.runs.schedules.tables import OccurrenceRow, ScheduleRow
from a13n_service.runs.schemas import Outcome
from a13n_service.runs.tables import RunRow, ThreadRow
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from .test_schedule_dispatch import schedule_for, trigger

pytestmark = pytest.mark.anyio


async def subscribe(service, **filters):  # type: ignore[no-untyped-def]
    response = await service.client.post(
        f"{service.api}/subscriptions",
        json={
            "name": "Schedule events",
            "url": "http://127.0.0.1:9/hook",
            "kinds": list(LIFECYCLE_KINDS),
            "filter": filters,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def deliveries(service, subscription_id):  # type: ignore[no-untyped-def]
    async with short_session(service.runtime.storage) as session:
        return [
            row.payload
            for row in (
                await session.scalars(
                    select(OutboxRow).where(OutboxRow.subscription_id == subscription_id).order_by(OutboxRow.created_at)
                )
            ).all()
        ]


async def test_completion_and_webhook_roll_back_together_then_emit_once(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    schedule, agent = await schedule_for(service, scripted_model, runs_kit)
    sub = await subscribe(service, schedule_id=schedule["id"], agent_id=agent["id"])
    wrong = await subscribe(service, schedule_id=new_object_id("sch"))
    item = await trigger(service, schedule, runs_kit)
    await dispatch.process(service.runtime, schedule["id"])
    async with short_session(service.runtime.storage) as session:
        stored = await session.get_one(OccurrenceRow, item["id"])
        root_id = stored.thread_id
        root = await session.get_one(ThreadRow, root_id)
        run_id = root.current_run_id
    before = await deliveries(service, sub)
    assert {event["type"] for event in before} == {"run.accepted", "schedule_occurrence.submitted"}
    # Both a final Run and its notice must disappear when the caller's transaction fails.
    with pytest.raises(RuntimeError, match="rollback"):
        async with transaction(service.runtime.storage) as session:
            thread = await session.get_one(ThreadRow, root_id, with_for_update=True)
            run = await session.get_one(RunRow, run_id, with_for_update=True)
            await seal.seal(session, service.runtime, thread, run, None, Outcome.cancelled(), at=await now(session))
            raise RuntimeError("rollback")
    assert await deliveries(service, sub) == before
    async with short_session(service.runtime.storage) as session:
        assert (await session.get_one(OccurrenceRow, item["id"])).status == "submitted"
        assert (await session.get_one(RunRow, run_id)).status == "accepted"

    # Competing cancellation requests use the ordinary Thread lock; only the winner generates completion.
    async def cancel():
        async with transaction(service.runtime.storage) as session:
            thread = await session.get_one(ThreadRow, root_id, with_for_update=True)
            run = await session.get_one(RunRow, run_id, with_for_update=True)
            await seal.stop(session, service.runtime, thread, run)

    await asyncio.gather(cancel(), cancel())
    events = await deliveries(service, sub)
    completed = [event for event in events if event["type"] == "schedule_occurrence.completed"]
    assert len(completed) == 1
    assert completed[0]["occurrence"]["execution_outcome"] == "cancelled"
    assert completed[0]["occurrence"]["completion_run_id"] == run_id
    assert completed[0]["occurrence"]["schedule_id"] == schedule["id"]
    assert not await deliveries(service, wrong)
    # Terminal evidence cannot be overwritten or reopened by direct persistence writes.
    with pytest.raises(DBAPIError, match="immutable"):
        async with transaction(service.runtime.storage) as session:
            stored = await session.get_one(OccurrenceRow, item["id"])
            stored.execution_outcome = "succeeded"


async def test_schedule_state_changes_cancel_pending_once_and_filter_missing_thread(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    schedule, _ = await schedule_for(service, scripted_model, runs_kit)
    sub = await subscribe(service, schedule_id=schedule["id"])
    thread_sub = await subscribe(service, schedule_id=schedule["id"], thread_id=new_object_id("thr"))
    url = f"{service.api}/schedules/{schedule['id']}"
    enabled = await service.client.post(url + "/enable", json={}, headers=runs_kit.if_match(schedule))
    assert enabled.status_code == 200
    schedule = enabled.json()
    await trigger(service, schedule, runs_kit)
    paused = await service.client.post(url + "/pause", json={}, headers=runs_kit.if_match(schedule))
    assert paused.status_code == 200
    again = await service.client.post(url + "/pause", json={}, headers=runs_kit.if_match(paused.json()))
    assert again.status_code == 200
    archived = await service.client.delete(url, headers=runs_kit.if_match(again.json()))
    assert archived.status_code == 204
    events = await deliveries(service, sub)
    assert sorted(e["type"] for e in events) == sorted(
        [
            "schedule.enabled",
            "schedule.paused",
            "schedule.archived",
            "schedule_occurrence.cancelled",
        ]
    )
    cancelled = next(e for e in events if "occurrence" in e)
    assert cancelled["occurrence"]["reason"] == "schedule_paused"
    assert not await deliveries(service, thread_sub)


@pytest.mark.parametrize("skip", [False, True])
async def test_once_schedule_finishes_scheduling_before_execution(service, scripted_model, runs_kit, skip):  # type: ignore[no-untyped-def]
    schedule, _ = await schedule_for(service, scripted_model, runs_kit)
    sub = await subscribe(service, schedule_id=schedule["id"])
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"])
        at = await now(session) - timedelta(seconds=600 if skip else 1)
        row.state, row.pause_reason = "enabled", None
        row.schedule_type, row.run_at = "once", at
        row.cron_expression = row.timezone = None
        row.next_fire_at = at
    await plan_next(service.runtime)
    await dispatch.process(service.runtime, schedule["id"])
    events = await deliveries(service, sub)
    assert sum(e["type"] == "schedule.completed" for e in events) == 1
    assert not any(e["type"] == "schedule_occurrence.completed" for e in events)
    occurrence_event = next(e for e in events if e["type"].startswith("schedule_occurrence."))
    assert occurrence_event["type"] == ("schedule_occurrence.skipped" if skip else "schedule_occurrence.submitted")
    if not skip:
        scripted_model.say("Done")
        await (await runs_kit.attempt(service))
        completed = [e for e in await deliveries(service, sub) if e["type"] == "schedule_occurrence.completed"]
        assert len(completed) == 1 and completed[0]["occurrence"]["execution_outcome"] == "succeeded"


async def test_permanent_submission_failure_notifies_and_auto_pauses(service, scripted_model, runs_kit, monkeypatch):  # type: ignore[no-untyped-def]
    schedule, _ = await schedule_for(service, scripted_model, runs_kit)
    sub = await subscribe(service, schedule_id=schedule["id"])
    await trigger(service, schedule, runs_kit)
    monkeypatch.setattr(
        dispatch, "prepare_thread", AsyncMock(side_effect=ServiceError("forbidden", "Test authority revoked"))
    )
    await dispatch.process(service.runtime, schedule["id"])
    events = await deliveries(service, sub)
    assert {e["type"] for e in events} == {"schedule.paused", "schedule_occurrence.failed"}
    assert next(e["schedule"] for e in events if "schedule" in e)["pause_reason"] == "authority_invalid"
    assert next(e["occurrence"] for e in events if "occurrence" in e)["reason"] == "authority_invalid"


async def test_child_completion_and_waiting_do_not_complete_root_occurrence(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.delegating(service, scripted_model, "async", user_questions=True)
    response = await service.client.post(
        f"{service.api}/schedules",
        headers=runs_kit.fresh_key(),
        json={
            "name": "Delegation",
            "schedule": {"expression": "0 9 * * *", "timezone": "UTC"},
            "invocation": {
                "agent_id": agent["id"],
                "agent_revision_id": None,
                "payload": {"content": [{"type": "text", "text": "Delegate and ask"}]},
            },
        },
    )
    assert response.status_code == 201
    schedule = response.json()
    sub = await subscribe(service, schedule_id=schedule["id"])
    item = await trigger(service, schedule, runs_kit)
    await dispatch.process(service.runtime, schedule["id"])
    scripted_model.call(
        "delegate", {"subagent_name": "helper", "prompt": "compute"}, call_id="call_d", to="Role: coordinator"
    )
    scripted_model.call(
        "ask_user_question", {"questions": [runs_kit.QUESTION]}, call_id="call_q", to="Role: coordinator"
    )
    await (await runs_kit.attempt(service))
    scripted_model.say("42", to="Role: worker")
    await (await runs_kit.attempt(service))
    async with short_session(service.runtime.storage) as session:
        stored = await session.get_one(OccurrenceRow, item["id"])
        assert stored.status == "submitted" and stored.completion_run_id is None
        thread = await session.get_one(ThreadRow, stored.thread_id)
        waiting_run = thread.last_run_id
    assert not any(e["type"] == "schedule_occurrence.completed" for e in await deliveries(service, sub))
    resumed = await service.client.post(
        f"{service.api}/runs/{waiting_run}/resume",
        headers=runs_kit.fresh_key(),
        json={"approvals": {}, "calls": {"call_q": {"status": "returned", "value": {"response": "blue"}}}},
    )
    assert resumed.status_code == 201, resumed.text
    scripted_model.say("Done", to="Role: coordinator")
    await (await runs_kit.attempt(service))
    events = await deliveries(service, sub)
    completed = [e for e in events if e["type"] == "schedule_occurrence.completed"]
    assert len(completed) == 1
    assert completed[0]["occurrence"]["completion_run_id"] == resumed.json()["id"]
    # Schedule filtering applies to child Run and Attempt notices, too.
    assert any(e.get("run", {}).get("thread_id") != thread.id and e["type"] == "run.completed" for e in events)
    assert any(e["type"] == "run_attempt.succeeded" for e in events)


async def test_later_run_does_not_complete_historical_occurrence(service, scripted_model, runs_kit, monkeypatch):  # type: ignore[no-untyped-def]
    schedule, agent = await schedule_for(service, scripted_model, runs_kit)
    sub = await subscribe(service, schedule_id=schedule["id"])
    item = await trigger(service, schedule, runs_kit)
    await dispatch.process(service.runtime, schedule["id"])
    with monkeypatch.context() as patch:
        patch.setattr(seal, "complete_occurrence", AsyncMock())
        scripted_model.say("Original result")
        await (await runs_kit.attempt(service))
    async with short_session(service.runtime.storage) as session:
        stored = await session.get_one(OccurrenceRow, item["id"])
        thread = await session.get_one(ThreadRow, stored.thread_id)
    continued = await runs_kit.submit(service, thread.id, runs_kit.message(agent, "Follow up"))
    assert continued.status_code == 201
    scripted_model.say("Later result")
    await (await runs_kit.attempt(service))
    async with short_session(service.runtime.storage) as session:
        stored = await session.get_one(OccurrenceRow, item["id"])
        assert stored.status == "submitted"
        assert stored.completion_run_id is None
        assert stored.completed_at is None
        assert stored.execution_outcome is None
    assert not any(e["type"] == "schedule_occurrence.completed" for e in await deliveries(service, sub))
