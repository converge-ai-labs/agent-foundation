"""Schedule observation keeps recent history bounded without losing pending or final state."""

import asyncio

import pytest
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.runs.schedules import dispatch, lifecycle, observation
from a13n_service.runs.schedules.tables import OccurrenceRow, ScheduleRow
from a13n_service.runs.session_state import repeatable

pytestmark = pytest.mark.anyio


async def create_schedule(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    response = await service.client.post(
        f"{service.api}/schedules",
        json={
            "name": "Observed report",
            "schedule": {"expression": "0 9 * * *", "timezone": "UTC"},
            "invocation": {
                "agent_id": agent["id"],
                "agent_revision_id": None,
                "payload": {"content": [{"type": "text", "text": "Report"}]},
            },
        },
        headers=runs_kit.fresh_key(),
    )
    assert response.status_code == 201, response.text
    return response.json(), agent


async def history_item(service, schedule, *, status="skipped", key="history"):  # type: ignore[no-untyped-def]
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"], with_for_update=True)
        item = await lifecycle.occurrence(
            session,
            row,
            await now(session),
            trigger="manual",
            status=status,
            reason="overlap" if status == "skipped" else None,
            requested_by_id=row.principal_id,
            request_key=key,
            request_digest=key,
        )
        return item.id


async def test_schedule_stream_requires_explicit_view(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    schedule, agent = await create_schedule(service, scripted_model, runs_kit)
    url = f"{service.api}/sessions/{schedule['session_id']}/stream"
    response = await service.client.get(url)
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "schedule_stream_view_required"
    ordinary = await runs_kit.start_thread(service, agent, "ordinary")
    response = await service.client.get(
        f"{service.api}/sessions/{ordinary['thread']['session_id']}/stream?view=schedule"
    )
    assert response.status_code == 400
    assert (await service.client.get(url + "?view=schedule&history_limit=101")).status_code == 400


async def test_empty_session_observes_pending_submission_and_entry_without_exposing_intent(
    service, scripted_model, runs_kit, listen
) -> None:  # type: ignore[no-untyped-def]
    schedule, _ = await create_schedule(service, scripted_model, runs_kit)
    headers = await runs_kit.bearer(service)
    async with listen(service.app) as base:
        url = f"{base}{service.api}/sessions/{schedule['session_id']}/stream?view=schedule"
        async with runs_kit.frames(url, headers) as stream:
            event, _, state = await anext(stream)
            assert event == "schedule" and state["schedule_id"] == schedule["id"]
            assert state["progress"]["pending_occurrence_id"] is None
            assert "invocation" not in state["configuration"] and "authority" not in state["configuration"]
            item_id = await history_item(service, schedule, status="pending")
            pending = (await runs_kit.until(stream, lambda frame: frame[0] == "occurrence"))[-1][2]
            assert pending["occurrence"]["id"] == item_id
            assert pending["occurrence"]["status"] == "pending"
            assert "snapshot" not in pending["occurrence"] and "authority" not in pending["occurrence"]
            await dispatch.process(service.runtime, schedule["id"])
            frames = await runs_kit.until(stream, lambda frame: frame[0] == "items")
            submitted = next(data for kind, _, data in frames if kind == "occurrence")
            thread = next(data for kind, _, data in frames if kind == "thread")
            assert submitted["occurrence"]["status"] == "submitted"
            assert thread["thread"]["schedule_occurrence_id"] == item_id
            assert thread["entries"][0]["status"] == "assigned"
            assert thread["current_run"]["schedule_origin"]["occurrence_id"] == item_id


async def test_pending_outside_recent_window_sends_terminal_state_once(
    service, scripted_model, runs_kit, listen
) -> None:  # type: ignore[no-untyped-def]
    schedule, _ = await create_schedule(service, scripted_model, runs_kit)
    pending_id = await history_item(service, schedule, status="pending", key="pending")
    recent_id = await history_item(service, schedule)
    headers = await runs_kit.bearer(service)
    async with listen(service.app) as base:
        url = f"{base}{service.api}/sessions/{schedule['session_id']}/stream?view=schedule&history_limit=1"
        async with runs_kit.frames(url, headers) as stream:
            initial = [await anext(stream) for _ in range(3)]
            assert {data["occurrence"]["id"] for kind, _, data in initial if kind == "occurrence"} == {
                pending_id,
                recent_id,
            }
            async with transaction(service.runtime.storage) as session:
                row = await session.get_one(ScheduleRow, schedule["id"], with_for_update=True)
                await lifecycle.cancel_pending(session, row, await now(session), "schedule_paused")
            frames = await runs_kit.until(
                stream, lambda frame: frame[0] == "occurrence" and frame[2]["occurrence"]["id"] == pending_id
            )
            assert frames[-1][2]["occurrence"]["status"] == "cancelled"
        async with runs_kit.frames(url, headers) as stream:
            assert (await anext(stream))[0] == "schedule"
            assert (await anext(stream))[2]["occurrence"]["id"] == recent_id
    async with short_session(service.runtime.storage) as session:
        await repeatable(session)
        snapshot = await observation.snapshot(session, schedule["workspace_id"], schedule["session_id"], 1)
        assert [item.occurrence.id for item in snapshot.occurrences] == [recent_id]
        assert snapshot.thread_ids == [] and snapshot.departing_occurrences == []
        assert (await session.get_one(OccurrenceRow, pending_id)).status == "cancelled"


async def test_active_thread_outside_window_finishes_before_retirement(
    service, scripted_model, runs_kit, listen
) -> None:  # type: ignore[no-untyped-def]
    schedule, agent = await create_schedule(service, scripted_model, runs_kit)
    item_id = await history_item(service, schedule, status="pending")
    await dispatch.process(service.runtime, schedule["id"])
    async with short_session(service.runtime.storage) as session:
        thread_id = (await session.get_one(OccurrenceRow, item_id)).thread_id
    recent = await history_item(service, schedule, key="newer")
    # An independent Thread is not owned by the Schedule, but its active work is observable.
    independent = await runs_kit.start_thread(service, agent, "Independent", session_id=schedule["session_id"])
    headers = await runs_kit.bearer(service)
    gate = asyncio.Event()
    scripted_model.say("Final report", gate=gate)
    async with listen(service.app) as base:
        url = f"{base}{service.api}/sessions/{schedule['session_id']}/stream?view=schedule&history_limit=1"
        async with runs_kit.frames(url, headers) as stream:
            initial = await runs_kit.until(
                stream, lambda frame: frame[0] == "items" and frame[2]["thread_id"] == independent["thread"]["id"]
            )
            assert {data["thread"]["id"] for kind, _, data in initial if kind == "thread"} == {
                thread_id,
                independent["thread"]["id"],
            }
            task = await runs_kit.attempt(service)
            try:
                gate.set()
                final = await runs_kit.until(stream, lambda frame: frame[0] == "items" and frame[2]["complete"])
                assert final[-1][2]["thread_id"] == thread_id
                assert ("assistant", "Final report") in runs_kit.texts(final[-1][2])
            finally:
                gate.set()
                await task
        async with runs_kit.frames(url, headers) as stream:
            reopened = await runs_kit.until(stream, lambda frame: frame[0] == "items")
            assert [data["occurrence"]["id"] for kind, _, data in reopened if kind == "occurrence"] == [recent]
            assert [data["thread"]["id"] for kind, _, data in reopened if kind == "thread"] == [
                independent["thread"]["id"],
            ]
        # A larger concurrent window may still watch the old root, but it cannot enlarge the smaller view.
        async with runs_kit.frames(url.replace("history_limit=1", "history_limit=3"), headers) as large:
            older = await runs_kit.until(large, lambda frame: frame[0] == "items")
            assert thread_id in [data["thread"]["id"] for kind, _, data in older if kind == "thread"]
            async with runs_kit.frames(url, headers) as small:
                current = await runs_kit.until(small, lambda frame: frame[0] == "items")
                assert thread_id not in [data["thread"]["id"] for kind, _, data in current if kind == "thread"]
