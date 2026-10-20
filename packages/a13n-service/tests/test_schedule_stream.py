"""Session streams add Schedule state without filtering Threads or requiring a view."""

import asyncio

import pytest
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.runs.schedules import dispatch, lifecycle, observation
from a13n_service.runs.schedules.tables import OccurrenceRow, ScheduleRow
from a13n_service.runs.session_state import Scope, repeatable
from a13n_service.runs.tables import ThreadRow

pytestmark = pytest.mark.anyio


async def create_schedule(service, scripted_model, runs_kit, *, agent=None):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    if agent is None:
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


async def test_schedule_state_is_scoped_to_its_session(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    schedule, agent = await create_schedule(service, scripted_model, runs_kit)
    other, _ = await create_schedule(service, scripted_model, runs_kit, agent=agent)
    own_item = await history_item(service, schedule)
    await history_item(service, other, key="other-history")
    ordinary = await runs_kit.start_thread(service, agent, "ordinary")
    scope = Scope(schedule["workspace_id"], schedule["session_id"])
    ordinary_scope = Scope(schedule["workspace_id"], ordinary["thread"]["session_id"])
    async with short_session(service.runtime.storage) as session:
        await repeatable(session)
        snapshots = await observation.snapshots(session, [scope, ordinary_scope])
    assert set(snapshots) == {scope}
    assert [item.occurrence.id for item in snapshots[scope].occurrences] == [own_item]
    operation = service.app.openapi()["paths"]["/api/v1/sessions/{session_id}/stream"]["get"]
    assert not {"view", "history_limit"} & {parameter["name"] for parameter in operation["parameters"]}


async def test_empty_session_observes_pending_submission_and_entry_without_exposing_intent(
    service, scripted_model, runs_kit, listen
) -> None:  # type: ignore[no-untyped-def]
    schedule, _ = await create_schedule(service, scripted_model, runs_kit)
    headers = await runs_kit.bearer(service)
    async with listen(service.app) as base:
        url = f"{base}{service.api}/sessions/{schedule['session_id']}/stream"
        async with runs_kit.frames(url, headers) as stream:
            event, _, state = await anext(stream)
            assert event == "schedule" and state["schedule_id"] == schedule["id"]
            assert state["progress"]["pending_occurrence_id"] is None
            assert "invocation" not in state["configuration"] and "authority" not in state["configuration"]
            async with transaction(service.runtime.storage) as session:
                row = await session.get_one(ScheduleRow, schedule["id"], with_for_update=True)
                row.name = "Updated report"
            changed = (await runs_kit.until(stream, lambda frame: frame[0] == "schedule"))[-1][2]
            assert changed["configuration"]["name"] == "Updated report"
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


async def test_occurrences_are_not_limited_to_recent_history_and_reconnect_keeps_terminal_state(
    service, scripted_model, runs_kit, listen
) -> None:  # type: ignore[no-untyped-def]
    schedule, _ = await create_schedule(service, scripted_model, runs_kit)
    pending_id = await history_item(service, schedule, status="pending", key="pending")
    recent_ids = {await history_item(service, schedule, key=f"history-{index}") for index in range(24)}
    headers = await runs_kit.bearer(service)
    async with listen(service.app) as base:
        url = f"{base}{service.api}/sessions/{schedule['session_id']}/stream"
        async with runs_kit.frames(url, headers) as stream:
            initial = [await anext(stream) for _ in range(26)]
            assert {data["occurrence"]["id"] for kind, _, data in initial if kind == "occurrence"} == {
                pending_id,
                *recent_ids,
            }
            async with transaction(service.runtime.storage) as session:
                row = await session.get_one(ScheduleRow, schedule["id"], with_for_update=True)
                await lifecycle.cancel_pending(session, row, await now(session), "schedule_paused")
            frames = await runs_kit.until(
                stream, lambda frame: frame[0] == "occurrence" and frame[2]["occurrence"]["id"] == pending_id
            )
            assert frames[-1][2]["occurrence"]["status"] == "cancelled"
        async with runs_kit.frames(url, headers) as stream:
            reopened = [await anext(stream) for _ in range(26)]
            records = {
                data["occurrence"]["id"]: data["occurrence"] for kind, _, data in reopened if kind == "occurrence"
            }
            assert set(records) == {pending_id, *recent_ids}
            assert records[pending_id]["status"] == "cancelled"


async def test_all_session_threads_remain_observable_after_completion_and_reconnect(
    service, scripted_model, runs_kit, listen
) -> None:  # type: ignore[no-untyped-def]
    schedule, agent = await create_schedule(service, scripted_model, runs_kit)
    item_id = await history_item(service, schedule, status="pending")
    await dispatch.process(service.runtime, schedule["id"])
    async with short_session(service.runtime.storage) as session:
        thread_id = (await session.get_one(OccurrenceRow, item_id)).thread_id
    recent = {await history_item(service, schedule, key=f"newer-{index}") for index in range(24)}
    # An independent Thread is not owned by the Schedule, but its active work is observable.
    independent = await runs_kit.start_thread(service, agent, "Independent", session_id=schedule["session_id"])
    headers = await runs_kit.bearer(service)
    gate = asyncio.Event()
    scripted_model.say("Final report", gate=gate)
    async with listen(service.app) as base:
        url = f"{base}{service.api}/sessions/{schedule['session_id']}/stream"
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
            assert {data["occurrence"]["id"] for kind, _, data in reopened if kind == "occurrence"} == {
                item_id,
                *recent,
            }
            assert {data["thread"]["id"] for kind, _, data in reopened if kind == "thread"} == {
                thread_id,
                independent["thread"]["id"],
            }
            async with transaction(service.runtime.storage) as session:
                thread = await session.get_one(ThreadRow, thread_id)
                thread.labels = {"observed": "after-completion"}
            changed = await runs_kit.until(
                stream,
                lambda frame: (
                    frame[0] == "thread"
                    and frame[2]["thread"]["id"] == thread_id
                    and frame[2]["thread"]["labels"] == {"observed": "after-completion"}
                ),
            )
            assert changed[-1][2]["current_run"] is None
