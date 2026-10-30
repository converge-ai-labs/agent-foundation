"""One-time schedules share durable submission, but finish without another calendar match."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.schedules import dispatch
from a13n_service.runs.schedules.observation import snapshots
from a13n_service.runs.schedules.sweep import Scheduler, plan_next
from a13n_service.runs.schedules.tables import OccurrenceRow, ScheduleRow
from a13n_service.runs.session_state import Scope
from a13n_service.runs.tables import InboxEntryRow, RunRow, SessionRow, ThreadRow
from sqlalchemy import func, select

pytestmark = pytest.mark.anyio


async def create_once(service, scripted_model, runs_kit, *, state="enabled"):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    async with short_session(service.runtime.storage) as session:
        future = await now(session) + timedelta(hours=1)
    body = {
        "name": "One report",
        "state": state,
        "schedule": {"type": "once", "run_at": future.isoformat()},
        "invocation": {
            "agent_id": agent["id"],
            "agent_revision_id": None,
            "payload": {"content": [{"type": "text", "text": "Report"}]},
        },
    }
    key = runs_kit.fresh_key()
    response = await service.client.post(f"{service.api}/schedules", json=body, headers=key)
    assert response.status_code == 201, response.text
    return response.json(), body, key


async def make_due(service, schedule, *, late_seconds=1):  # type: ignore[no-untyped-def]
    # Move the configured instant, before any immutable occurrence exists, without sleeping.
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"])
        row.run_at = await now(session) - timedelta(seconds=late_seconds)
        row.next_fire_at = row.run_at if row.state == "enabled" else None
    return (await service.client.get(f"{service.api}/schedules/{schedule['id']}")).json()


async def test_concurrent_once_planning_recovers_and_completes_before_run(
    service, scripted_model, runs_kit, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    schedule, body, key = await create_once(service, scripted_model, runs_kit)
    schedule = await make_due(service, schedule)
    ids = await asyncio.gather(*(plan_next(service.runtime) for _ in range(3)))
    assert ids.count(schedule["id"]) == 1
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"])
        item = (await session.scalars(select(OccurrenceRow))).one()
        assert row.state == "enabled" and row.next_fire_at is None
        assert row.pending_occurrence_id == item.id and item.status == "pending"
        assert row.version == schedule["version"]
        assert item.scheduled_at == item.covered_from_at == row.run_at
        assert item.snapshot["schedule"]["type"] == "once"
    # Recover after transaction A using a fresh scheduler. No Run is necessary for completion.
    monkeypatch.setattr(dispatch, "accept", AsyncMock(return_value=None))
    await asyncio.gather(Scheduler(service.runtime)(), Scheduler(service.runtime)())
    await Scheduler(service.runtime)()
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"])
        item = (await session.scalars(select(OccurrenceRow))).one()
        assert row.state == "completed" and row.next_fire_at is None and row.pending_occurrence_id is None
        assert row.version > schedule["version"]
        assert item.status == "submitted"
        assert await session.scalar(select(func.count()).select_from(ThreadRow)) == 1
        assert await session.scalar(select(func.count()).select_from(InboxEntryRow)) == 1
        assert await session.scalar(select(func.count()).select_from(RunRow)) == 0
        scope = Scope(row.workspace_id, row.session_id)
    async with short_session(service.runtime.storage) as session:
        observed = (await snapshots(session, [scope]))[scope]
        assert observed.schedules[0].configuration.state == "completed"
        assert observed.schedules[0].configuration.schedule.type == "once"
        assert observed.schedules[0].progress.next_fire_at is None
    # Replay still returns the original resource despite its now elapsed time and completed state.
    replay = await service.client.post(f"{service.api}/schedules", json=body, headers=key)
    assert replay.status_code == 200 and replay.json()["state"] == "completed"
    page = (await service.client.get(f"{service.api}/schedules", params={"state": "completed"})).json()
    assert [item["id"] for item in page["items"]] == [schedule["id"]]


@pytest.mark.parametrize("reason", ["missed_deadline", "overlap"])
async def test_once_skip_finishes_schedule(service, scripted_model, runs_kit, reason) -> None:  # type: ignore[no-untyped-def]
    schedule, _, _ = await create_once(service, scripted_model, runs_kit)
    if reason == "overlap":
        manual = await service.client.post(
            f"{service.api}/schedules/{schedule['id']}/dispatch",
            json={},
            headers={**runs_kit.if_match(schedule), **runs_kit.fresh_key()},
        )
        assert manual.status_code == 202
    await make_due(service, schedule, late_seconds=301 if reason == "missed_deadline" else 1)
    await plan_next(service.runtime)
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"])
        item = (await session.scalars(select(OccurrenceRow).where(OccurrenceRow.trigger == "scheduled"))).one()
        assert row.state == "completed" and row.next_fire_at is None
        assert item.status == "skipped" and item.reason == reason
    if reason == "overlap":
        # Existing manual intent remains valid even though the automatic trigger is finished.
        await dispatch.process(service.runtime, schedule["id"])
        async with short_session(service.runtime.storage) as session:
            assert (await session.get_one(ScheduleRow, schedule["id"])).state == "completed"
            assert (await session.get_one(OccurrenceRow, manual.json()["id"])).status == "submitted"


async def test_once_retry_keeps_intent_and_finishes_only_after_submission(
    service, scripted_model, runs_kit, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    schedule, _, _ = await create_once(service, scripted_model, runs_kit)
    await make_due(service, schedule)
    await plan_next(service.runtime)
    original = dispatch.prepare_thread
    monkeypatch.setattr(dispatch, "prepare_thread", AsyncMock(side_effect=ServiceError("unavailable", "Temporary")))
    await dispatch.process(service.runtime, schedule["id"])
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"])
        item = (await session.scalars(select(OccurrenceRow))).one()
        assert row.state == "enabled" and row.next_fire_at is None
        assert item.status == "pending" and item.dispatch_attempts == 1
        assert await session.scalar(select(func.count()).select_from(ThreadRow)) == 0
        item.next_attempt_at = await now(session)
    monkeypatch.setattr(dispatch, "prepare_thread", original)
    await Scheduler(service.runtime)()
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"])
        item = (await session.scalars(select(OccurrenceRow))).one()
        assert row.state == "completed"
        assert item.status == "submitted" and item.dispatch_attempts == 2


@pytest.mark.parametrize("code", ["forbidden", "not_found", "unavailable", "missed_deadline"])
async def test_once_final_submission_failure_completes_instead_of_pausing(
    service, scripted_model, runs_kit, monkeypatch, code
) -> None:  # type: ignore[no-untyped-def]
    schedule, _, _ = await create_once(service, scripted_model, runs_kit)
    await make_due(service, schedule)
    await plan_next(service.runtime)
    if code == "missed_deadline":
        async with short_session(service.runtime.storage) as session:
            item = (await session.scalars(select(OccurrenceRow))).one()
            monkeypatch.setattr(dispatch, "now", AsyncMock(return_value=item.deadline_at))
    monkeypatch.setattr(dispatch, "prepare_thread", AsyncMock(side_effect=ServiceError(code, "Do not retain")))
    settings = service.runtime.settings.model_copy(
        update={"schedules": service.runtime.settings.schedules.model_copy(update={"dispatch_max_attempts": 1})}
    )
    await dispatch.process(replace(service.runtime, settings=settings), schedule["id"])
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"])
        item = (await session.scalars(select(OccurrenceRow))).one()
        assert row.state == "completed" and row.pause_reason is None
        assert item.status == ("skipped" if code == "missed_deadline" else "failed")
        assert (
            item.reason
            == {
                "forbidden": "authority_invalid",
                "not_found": "target_invalid",
                "unavailable": "dispatch_attempts_exhausted",
                "missed_deadline": "missed_deadline",
            }[code]
        )
        assert await session.scalar(select(func.count()).select_from(ThreadRow)) == 0


@pytest.mark.parametrize("planned", [False, True])
async def test_once_pause_cancels_and_requires_new_future_time(service, scripted_model, runs_kit, planned) -> None:  # type: ignore[no-untyped-def]
    schedule, _, _ = await create_once(service, scripted_model, runs_kit)
    schedule = await make_due(service, schedule)
    if planned:
        await plan_next(service.runtime)
    url = f"{service.api}/schedules/{schedule['id']}"
    paused = await service.client.post(f"{url}/pause", json={}, headers=runs_kit.if_match(schedule))
    assert paused.status_code == 200, paused.text
    assert paused.json()["state"] == "paused"
    await Scheduler(service.runtime)()
    async with short_session(service.runtime.storage) as session:
        item = (await session.scalars(select(OccurrenceRow))).one()
        assert item.status == ("cancelled" if planned else "skipped")
        assert item.reason == "schedule_paused"
        assert await session.scalar(select(func.count()).select_from(ThreadRow)) == 0
        future = await now(session) + timedelta(hours=2)
    enable = await service.client.post(f"{url}/enable", json={}, headers=runs_kit.if_match(paused.json()))
    assert enable.status_code == 400
    # Metadata-only edits remain valid when the original time has passed.
    renamed = await service.client.patch(url, json={"name": "Later report"}, headers=runs_kit.if_match(paused.json()))
    assert renamed.status_code == 200
    updated = await service.client.patch(
        url,
        json={"schedule": {"type": "once", "run_at": future.isoformat()}},
        headers=runs_kit.if_match(renamed.json()),
    )
    assert updated.status_code == 200, updated.text
    enabled = await service.client.post(f"{url}/enable", json={}, headers=runs_kit.if_match(updated.json()))
    assert enabled.status_code == 200
    status = (await service.client.get(f"{url}/status")).json()
    assert datetime.fromisoformat(status["next_fire_at"]) == future


async def test_completed_once_is_read_only_except_archive(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    schedule, _, _ = await create_once(service, scripted_model, runs_kit)
    await make_due(service, schedule, late_seconds=301)
    await Scheduler(service.runtime)()
    url = f"{service.api}/schedules/{schedule['id']}"
    completed = (await service.client.get(url)).json()
    for command in ["enable", "pause", "dispatch"]:
        result = await service.client.post(
            f"{url}/{command}", json={}, headers={**runs_kit.if_match(completed), **runs_kit.fresh_key()}
        )
        assert result.status_code == 409, result.text
        assert result.json()["error"]["details"]["reason"] == "completed"
    result = await service.client.patch(url, json={"name": "Again"}, headers=runs_kit.if_match(completed))
    assert result.status_code == 409
    assert (await service.client.delete(url, headers=runs_kit.if_match(completed))).status_code == 204
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"])
        assert row.state == "archived"
        assert await session.get(SessionRow, row.session_id) is not None
        assert await session.scalar(select(func.count()).select_from(OccurrenceRow)) == 1


@pytest.mark.parametrize("planned", [False, True])
async def test_once_reschedule_cancels_old_intent_without_completing_replacement(
    service, scripted_model, runs_kit, planned
) -> None:  # type: ignore[no-untyped-def]
    schedule, body, _ = await create_once(service, scripted_model, runs_kit)
    schedule = await make_due(service, schedule)
    if planned:
        await plan_next(service.runtime)
    url = f"{service.api}/schedules/{schedule['id']}"
    updated = await service.client.patch(url, json={"schedule": body["schedule"]}, headers=runs_kit.if_match(schedule))
    assert updated.status_code == 200, updated.text
    assert updated.json()["state"] == "enabled"
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"])
        item = (await session.scalars(select(OccurrenceRow))).one()
        assert item.status == ("cancelled" if planned else "skipped")
        assert item.reason == "schedule_changed"
        assert row.next_fire_at == row.run_at > await now(session)
        assert row.pending_occurrence_id is None
    # Switching rules before the next trigger preserves the same resource and Session.
    cron = await service.client.patch(
        url,
        json={"schedule": {"expression": "0 9 * * *", "timezone": "UTC"}},
        headers=runs_kit.if_match(updated.json()),
    )
    assert cron.status_code == 200 and cron.json()["schedule"]["type"] == "cron"
    once = await service.client.patch(url, json={"schedule": body["schedule"]}, headers=runs_kit.if_match(cron.json()))
    assert once.status_code == 200 and once.json()["session_id"] == schedule["session_id"]


async def test_once_preview_normalization_validation_and_manual_dispatch(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    schedule, body, _ = await create_once(service, scripted_model, runs_kit, state="paused")
    url = f"{service.api}/schedules/{schedule['id']}"
    rule = {"type": "once", "run_at": "2090-10-21T09:00:00+08:00"}
    preview = await service.client.post(
        f"{service.api}/schedules/preview", json={"schedule": rule, "after": "2090-10-21T00:00:00Z", "count": 10}
    )
    assert preview.status_code == 200
    assert preview.json()["items"] == [{"utc": "2090-10-21T01:00:00Z", "local": "2090-10-21T01:00:00Z"}]
    empty = await service.client.post(
        f"{service.api}/schedules/preview", json={"schedule": rule, "after": "2090-10-21T01:00:00Z"}
    )
    assert empty.status_code == 200 and empty.json()["items"] == []
    for invalid in [
        {"type": "once", "run_at": "2090-10-21T09:00:00"},
        {"type": "once", "run_at": datetime(2000, 1, 1, tzinfo=UTC).isoformat()},
        {**rule, "expression": "* * * * *"},
        {"type": "once"},
    ]:
        bad = await service.client.post(
            f"{service.api}/schedules", json={**body, "schedule": invalid}, headers=runs_kit.fresh_key()
        )
        assert bad.status_code == 400, bad.text
    manual = await service.client.post(
        f"{url}/dispatch", json={}, headers={**runs_kit.if_match(schedule), **runs_kit.fresh_key()}
    )
    assert manual.status_code == 202
    await dispatch.process(service.runtime, schedule["id"])
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"])
        assert row.state == "paused" and row.last_processed_fire_at is None
        assert await session.scalar(select(func.count()).select_from(ScheduleRow)) == 1
