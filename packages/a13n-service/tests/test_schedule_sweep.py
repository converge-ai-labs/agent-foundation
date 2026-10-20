"""Due-time scans persist intent before submission and coordinate through PostgreSQL."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.runs.schedules import dispatch, lifecycle
from a13n_service.runs.schedules.sweep import Scheduler, plan_next, schedule_sweep
from a13n_service.runs.schedules.tables import OccurrenceRow, ScheduleRow
from a13n_service.runs.tables import ThreadRow
from sqlalchemy import func, select

pytestmark = pytest.mark.anyio


async def create_schedule(service, agent, runs_kit):  # type: ignore[no-untyped-def]
    response = await service.client.post(
        f"{service.api}/schedules",
        json={
            "name": "Minute report",
            "state": "enabled",
            "schedule": {"expression": "* * * * *", "timezone": "UTC"},
            "invocation": {
                "agent_id": agent["id"],
                "agent_revision_id": None,
                "payload": {"content": [{"type": "text", "text": "Report"}]},
            },
        },
        headers=runs_kit.fresh_key(),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_concurrent_planners_coalesce_backlog_then_recover_pending(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    schedule = await create_schedule(service, agent, runs_kit)
    async with transaction(service.runtime.storage) as session:
        current = await now(session)
        row = await session.get_one(ScheduleRow, schedule["id"])
        missed_from = current.replace(second=0, microsecond=0) - timedelta(hours=2)
        row.next_fire_at = missed_from
    planned = await asyncio.gather(*(plan_next(service.runtime) for _ in range(3)))
    assert planned.count(schedule["id"]) == 1
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"])
        item = (await session.scalars(select(OccurrenceRow))).one()
        assert item.status == "pending" and item.covered_from_at == missed_from
        assert item.scheduled_at == current.replace(second=0, microsecond=0)
        assert row.next_fire_at > await now(session) and row.version == schedule["version"]
        assert row.updated_at.isoformat().replace("+00:00", "Z") == schedule["updated_at"]
        assert await session.scalar(select(func.count()).select_from(ThreadRow)) == 0
        item_id = item.id
    # A new scheduler instance recovers committed intent; no in-memory timer or claim survives from A.
    await asyncio.gather(Scheduler(service.runtime)(), Scheduler(service.runtime)())
    async with short_session(service.runtime.storage) as session:
        assert (await session.get_one(OccurrenceRow, item_id)).status == "submitted"
        assert await session.scalar(select(func.count()).select_from(ThreadRow)) == 1
        assert await session.scalar(select(func.count()).select_from(OccurrenceRow)) == 1


async def test_missed_deadline_skips_latest_daily_time(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    schedule = await create_schedule(service, agent, runs_kit)
    async with transaction(service.runtime.storage) as session:
        current = await now(session)
        planned = current.replace(second=0, microsecond=0) - timedelta(minutes=10)
        row = await session.get_one(ScheduleRow, schedule["id"])
        row.cron_expression = f"{planned.minute} {planned.hour} * * *"
        row.next_fire_at = planned - timedelta(days=30)
    await Scheduler(service.runtime)()
    async with short_session(service.runtime.storage) as session:
        item = (await session.scalars(select(OccurrenceRow))).one()
        row = await session.get_one(ScheduleRow, schedule["id"])
        assert item.status == "skipped" and item.reason == "missed_deadline"
        assert item.scheduled_at == planned and item.next_attempt_at is None
        assert row.next_fire_at == planned + timedelta(days=1)
        assert await session.scalar(select(func.count()).select_from(ThreadRow)) == 0


async def test_overlap_retains_existing_pending_and_advances_periodic_time(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    schedule = await create_schedule(service, agent, runs_kit)
    async with transaction(service.runtime.storage) as session:
        current = await now(session)
        row = await session.get_one(ScheduleRow, schedule["id"], with_for_update=True)
        pending = await lifecycle.occurrence(
            session,
            row,
            current,
            trigger="manual",
            requested_by_id=row.principal_id,
            request_key="manual",
            request_digest="manual",
        )
        pending.next_attempt_at = current + timedelta(minutes=2)
        pending_id = pending.id
        row.next_fire_at = current.replace(second=0, microsecond=0) - timedelta(minutes=1)
    await Scheduler(service.runtime)()
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"])
        assert row.pending_occurrence_id == pending_id
        item = (await session.scalars(select(OccurrenceRow).where(OccurrenceRow.trigger == "scheduled"))).one()
        assert item.status == "skipped" and item.reason == "overlap"
        assert row.next_fire_at > await now(session)
        assert await session.scalar(select(func.count()).select_from(ThreadRow)) == 0


async def test_each_queue_gets_its_own_batch_allowance(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    schedules = [await create_schedule(service, agent, runs_kit) for _ in range(3)]
    async with transaction(service.runtime.storage) as session:
        current = await now(session)
        for index, schedule in enumerate(schedules):
            row = await session.get_one(ScheduleRow, schedule["id"], with_for_update=True)
            if index < 2:
                await lifecycle.occurrence(
                    session,
                    row,
                    current,
                    trigger="manual",
                    requested_by_id=row.principal_id,
                    request_key=f"manual-{index}",
                    request_digest=f"manual-{index}",
                )
            else:
                row.next_fire_at = current.replace(second=0, microsecond=0) - timedelta(minutes=1)
    config = service.runtime.settings.model_copy(
        update={"schedules": service.runtime.settings.schedules.model_copy(update={"batch": 1})}
    )
    await Scheduler(replace(service.runtime, settings=config))()
    async with short_session(service.runtime.storage) as session:
        items = (await session.scalars(select(OccurrenceRow))).all()
        assert sum(item.trigger == "manual" and item.status == "submitted" for item in items) == 1
        assert sum(item.trigger == "scheduled" and item.status == "submitted" for item in items) == 1
        assert sum(item.status == "pending" for item in items) == 1


async def test_pause_between_plan_and_submit_and_processed_clock_floor(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    schedule = await create_schedule(service, agent, runs_kit)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"])
        row.next_fire_at = (await now(session)).replace(second=0, microsecond=0) - timedelta(minutes=1)
    await plan_next(service.runtime)
    paused = await service.client.post(
        f"{service.api}/schedules/{schedule['id']}/pause", json={}, headers=runs_kit.if_match(schedule)
    )
    assert paused.status_code == 200
    await dispatch.process(service.runtime, schedule["id"])
    async with transaction(service.runtime.storage) as session:
        item = (await session.scalars(select(OccurrenceRow))).one()
        assert item.status == "cancelled" and item.reason == "schedule_paused"
        row = await session.get_one(ScheduleRow, schedule["id"])
        future = await now(session) + timedelta(hours=1)
        row.last_processed_fire_at = future
        current_version = row.version
    enabled = await service.client.post(
        f"{service.api}/schedules/{schedule['id']}/enable",
        json={},
        headers={"if-match": f'"{schedule["id"]}:{current_version}"'},
    )
    assert enabled.status_code == 200, enabled.text
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"])
        assert row.next_fire_at > future
        assert await session.scalar(select(func.count()).select_from(ThreadRow)) == 0


async def test_control_sweep_automatically_submits_without_browser(serve, settings, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    config = settings.model_copy(update={"schedules": settings.schedules.model_copy(update={"scan_seconds": 0.05})})
    async with serve(settings=config) as service:
        agent = await runs_kit.create_agent(service, scripted_model)
        schedule = await create_schedule(service, agent, runs_kit)
        async with transaction(service.runtime.storage) as session:
            row = await session.get_one(ScheduleRow, schedule["id"])
            row.next_fire_at = (await now(session)).replace(second=0, microsecond=0) - timedelta(minutes=1)
        async with asyncio.timeout(5):
            while True:
                async with short_session(service.runtime.storage) as session:
                    item = await session.scalar(
                        select(OccurrenceRow).where(OccurrenceRow.schedule_id == schedule["id"])
                    )
                    if item is not None and item.status == "submitted":
                        break
                await asyncio.sleep(0.02)
        assert item.thread_id and item.entry_id
        sweep = schedule_sweep(service.runtime)
        assert sweep.name == "dispatch_schedules" and sweep.timeout == 30 and sweep.every == 0.05


async def test_locked_schedule_does_not_block_other_due_work(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    first, second = [await create_schedule(service, agent, runs_kit) for _ in range(2)]
    async with transaction(service.runtime.storage) as session:
        current = (await now(session)).replace(second=0, microsecond=0)
        for schedule in (first, second):
            row = await session.get_one(ScheduleRow, schedule["id"])
            row.next_fire_at = current - timedelta(minutes=1)
    async with transaction(service.runtime.storage) as session:
        await session.get_one(ScheduleRow, first["id"], with_for_update=True)
        async with asyncio.timeout(2):
            assert await plan_next(service.runtime) == second["id"]
    assert await plan_next(service.runtime) == first["id"]
    await dispatch.process(service.runtime, first["id"])
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, first["id"], with_for_update=True)
        # Repairing stale scan progress cannot change the unique instant's terminal evidence.
        row.next_fire_at = current - timedelta(minutes=1)
    assert await plan_next(service.runtime) == first["id"]
    async with short_session(service.runtime.storage) as session:
        items = (await session.scalars(select(OccurrenceRow).where(OccurrenceRow.schedule_id == first["id"]))).all()
        assert len(items) == 1 and items[0].status == "submitted"


@pytest.mark.parametrize("completed", [False, True])
async def test_planning_checks_root_completion_and_ignores_later_conversation(
    service, scripted_model, runs_kit, completed
) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    schedule = await create_schedule(service, agent, runs_kit)
    response = await service.client.post(
        f"{service.api}/schedules/{schedule['id']}/dispatch",
        json={},
        headers={**runs_kit.if_match(schedule), **runs_kit.fresh_key()},
    )
    assert response.status_code == 202, response.text
    await dispatch.process(service.runtime, schedule["id"])
    if completed:
        scripted_model.say("Done")
        await (await runs_kit.attempt(service))
        async with short_session(service.runtime.storage) as session:
            previous = await session.get_one(OccurrenceRow, response.json()["id"])
        continued = await runs_kit.submit(service, previous.thread_id, runs_kit.message(agent, "Follow up"))
        assert continued.status_code == 201, continued.text
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"])
        row.next_fire_at = (await now(session)).replace(second=0, microsecond=0) - timedelta(minutes=1)
    await plan_next(service.runtime)
    async with short_session(service.runtime.storage) as session:
        item = (await session.scalars(select(OccurrenceRow).where(OccurrenceRow.trigger == "scheduled"))).one()
        assert item.status == ("pending" if completed else "skipped")
        assert item.reason == (None if completed else "overlap")
