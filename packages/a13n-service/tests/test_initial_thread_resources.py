"""Direct and scheduled creation install initial resources before the first Run."""

from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_service.distribution import OSS
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.providers.registry import Registry
from a13n_service.runs import accept
from a13n_service.runs.browsers.tables import BrowserRow, ThreadBrowserRow
from a13n_service.runs.schedules import dispatch
from a13n_service.runs.schedules.tables import OccurrenceRow, ScheduleRow
from a13n_service.runs.tables import InboxEntryRow, RunRow, SessionRow, ThreadRow
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from .environments_support import EXPIRING, FAKE, reserve, with_fake_providers
from .test_browsers import command, setup
from .test_schedule_dispatch import trigger

pytestmark = pytest.mark.anyio


@pytest.fixture
async def resources(service, runs_kit):
    await with_fake_providers(service)
    environment = await reserve(service, service.template["id"])
    supplier, browser = await setup(service, runs_kit)
    fake_browser = service.runtime.registry.get("browser", "fake_browser")
    service.runtime = replace(service.runtime, registry=Registry.of((*OSS.providers, FAKE, EXPIRING, fake_browser)))
    service.app.state.runtime = service.runtime
    return {
        "environments": [{"name": "workspace", "environment_id": environment["id"]}],
        "browser_binding": {"browser_id": browser["id"], "tab_access": "thread", "preparation": "first_use"},
    }, supplier


def assert_snapshot(run, fields):
    assert run["browser_binding"] == fields["browser_binding"]
    [mount] = run["environment_mounts"]
    assert {key: mount[key] for key in ("name", "environment_id")} == fields["environments"][0]


async def assert_desired(service, thread_id, fields):
    path = f"{service.api}/threads/{thread_id}"
    assert (await service.client.get(f"{path}/browser")).json() == fields["browser_binding"]
    [mount] = (await service.client.get(f"{path}/environments")).json()["items"]
    assert {key: mount[key] for key in ("name", "environment_id")} == fields["environments"][0]


async def create_schedule(service, runs_kit, agent, fields, **extra):
    return await service.client.post(
        f"{service.api}/schedules",
        json={
            "name": "Scheduled report",
            "schedule": {"expression": "0 9 * * *", "timezone": "UTC"},
            "invocation": {
                "agent_id": agent["id"],
                "agent_revision_id": None,
                "payload": {"content": [{"type": "text", "text": "Report today"}]},
                **fields,
            },
            **extra,
        },
        headers=runs_kit.fresh_key(),
    )


async def test_new_thread_freezes_both_resources_and_replays(service, runs_kit, resources):
    fields, supplier = resources
    body = runs_kit.message(service.agent, "First input", **fields)
    path = f"{service.api}/threads"
    headers = runs_kit.fresh_key()
    response = await service.client.post(path, json=body, headers=headers)
    assert response.status_code == 201, response.text
    first = response.json()
    assert_snapshot(first["run"], fields)
    await assert_desired(service, first["thread"]["id"], fields)
    replay = await service.client.post(path, json=body, headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json()["run"]["id"] == first["run"]["id"]
    assert supplier.creates == 0


async def test_fresh_schedule_reuses_resources_in_two_independent_threads(service, scripted_model, runs_kit, resources):
    fields, supplier = resources
    agent = await runs_kit.create_agent(service, scripted_model)
    created = await create_schedule(service, runs_kit, agent, fields)
    assert created.status_code == 201, created.text
    schedule = created.json()
    thread_ids = set()
    for index in range(2):
        occurrence = await trigger(service, schedule, runs_kit, key=f"trigger-{index}")
        await dispatch.process(service.runtime, schedule["id"])
        async with short_session(service.runtime.storage) as session:
            item = await session.get_one(OccurrenceRow, occurrence["id"])
            assert item.status == "submitted" and item.thread_id and item.entry_id
            entry = await session.get_one(InboxEntryRow, item.entry_id)
            assert entry.assigned_run_id
            thread_id, run_id = item.thread_id, entry.assigned_run_id
        thread_ids.add(thread_id)
        await assert_desired(service, thread_id, fields)
        assert_snapshot((await service.client.get(f"{service.api}/runs/{run_id}")).json(), fields)
        scripted_model.say(f"Report {index}")
        await (await runs_kit.attempt(service))
        async with short_session(service.runtime.storage) as session:
            item = await session.get_one(OccurrenceRow, occurrence["id"])
            assert item.status == "completed" and item.execution_outcome == "succeeded"
    assert len(thread_ids) == 2
    assert supplier.creates == 0


async def test_browser_lock_precedes_fresh_environment_reservation(
    service, scripted_model, runs_kit, resources, monkeypatch
):
    fields, _ = resources
    binding = fields["browser_binding"]
    agent = await runs_kit.create_agent(service, scripted_model)
    first = await runs_kit.start_thread(service, agent, "First input", **fields)
    scripted_model.say("Ready")
    await (await runs_kit.attempt(service))
    reserve_environment = accept.reserve
    checked = False

    async def reserve_with_lock_check(session, *args, **kwargs):
        nonlocal checked
        # The current acceptance must own the Browser before acquiring the workspace reservation lock.
        with pytest.raises(OperationalError, match="could not obtain lock"):
            async with transaction(service.runtime.storage) as observer:
                await observer.scalar(
                    select(BrowserRow.id).where(BrowserRow.id == binding["browser_id"]).with_for_update(nowait=True)
                )
        checked = True
        return await reserve_environment(session, *args, **kwargs)

    monkeypatch.setattr(accept, "reserve", reserve_with_lock_check)
    submitted = await runs_kit.submit(
        service,
        first["thread"]["id"],
        runs_kit.message(
            agent, "Fresh workspace", options={"environment": {"mode": "fresh", "template_id": service.template["id"]}}
        ),
    )
    assert submitted.status_code == 201, submitted.text
    run = submitted.json()["run"]
    assert checked and run["browser_binding"] == binding
    assert run["environment_mounts"][0]["environment_id"] != fields["environments"][0]["environment_id"]


@pytest.mark.parametrize("failure", ["released", "expired"])
async def test_invalid_browser_refuses_dispatch_without_leaving_work(
    service, scripted_model, runs_kit, resources, failure
):
    fields, _ = resources
    agent = await runs_kit.create_agent(service, scripted_model)
    created = await create_schedule(service, runs_kit, agent, fields)
    assert created.status_code == 201, created.text
    schedule = created.json()
    occurrence = await trigger(service, schedule, runs_kit)
    browser_id = fields["browser_binding"]["browser_id"]
    if failure == "released":
        await command(service, runs_kit, browser_id, "release")
    else:
        async with transaction(service.runtime.storage) as session:
            browser = await session.get_one(BrowserRow, browser_id)
            browser.expires_at = await now(session) - timedelta(seconds=1)
    rejected = await service.client.post(
        f"{service.api}/threads", json=runs_kit.message(agent, "First input", **fields), headers=runs_kit.fresh_key()
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["details"]["reason"] == f"browser_{'deleted' if failure == 'released' else failure}"
    await dispatch.process(service.runtime, schedule["id"])
    async with short_session(service.runtime.storage) as session:
        item = await session.get_one(OccurrenceRow, occurrence["id"])
        row = await session.get_one(ScheduleRow, schedule["id"])
        assert item.status == "failed" and item.reason == row.pause_reason == "target_invalid"
        assert item.last_error == "conflict" and item.thread_id is None and item.entry_id is None
        for table in (ThreadRow, ThreadBrowserRow, InboxEntryRow, RunRow):
            assert await session.scalar(select(func.count()).select_from(table)) == 0
        assert await session.scalar(select(func.count()).select_from(SessionRow)) == 1  # The Schedule's Session.


async def test_fresh_schedule_rolls_back_initial_mounts_when_run_acceptance_fails(service, runs_kit, resources):
    fields, _ = resources
    created = await create_schedule(
        service,
        runs_kit,
        service.agent,
        {
            "environments": fields["environments"],
            "options": {"environment": {"mode": "existing", "environment_id": new_object_id("env")}},
        },
    )
    assert created.status_code == 201, created.text
    schedule = created.json()
    occurrence = await trigger(service, schedule, runs_kit)
    await dispatch.process(service.runtime, schedule["id"])
    async with short_session(service.runtime.storage) as session:
        item = await session.get_one(OccurrenceRow, occurrence["id"])
        row = await session.get_one(ScheduleRow, schedule["id"])
        assert item.status == "failed" and item.reason == row.pause_reason == "target_invalid"
        assert item.last_error == "not_found" and item.thread_id is None and item.entry_id is None
        for table in (ThreadRow, InboxEntryRow, RunRow):
            assert await session.scalar(select(func.count()).select_from(table)) == 0


@pytest.mark.parametrize("field", ["browser_binding", "environments"])
async def test_fixed_schedule_target_requires_clearing_initial_resources(service, runs_kit, resources, field):
    fields, _ = resources
    initial = {field: fields[field]}
    agent = service.agent
    fixed = {"mode": "thread"}
    invalid = await create_schedule(service, runs_kit, agent, initial, target=fixed)
    assert invalid.status_code == 400, invalid.text
    created = await create_schedule(service, runs_kit, agent, initial)
    assert created.status_code == 201, created.text
    schedule = created.json()
    item = await trigger(service, schedule, runs_kit)
    path = f"{service.api}/schedules/{schedule['id']}"
    headers = runs_kit.if_match(schedule)
    invalid = await service.client.patch(path, json={"target": fixed}, headers=headers)
    assert invalid.status_code == 400, invalid.text
    invocation = {key: value for key, value in schedule["invocation"].items() if key != field}
    updated = await service.client.patch(path, json={"target": fixed, "invocation": invocation}, headers=headers)
    assert updated.status_code == 200, updated.text
    async with short_session(service.runtime.storage) as session:
        stored = await session.get_one(OccurrenceRow, item["id"])
        assert stored.status == "cancelled" and stored.reason == "schedule_changed"
    invalid = await service.client.patch(
        path, json={"invocation": schedule["invocation"]}, headers=runs_kit.if_match(updated.json())
    )
    assert invalid.status_code == 400, invalid.text
