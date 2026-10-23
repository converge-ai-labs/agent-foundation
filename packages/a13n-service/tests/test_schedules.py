"""Schedule management crosses HTTP, authorization and PostgreSQL transaction boundaries."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.runs.schedules import service as schedules
from a13n_service.runs.schedules.schemas import ScheduleCreate, ScheduleUpdate
from a13n_service.runs.schedules.tables import OccurrenceRow, ScheduleRow
from a13n_service.runs.tables import SessionRow, ThreadRow
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal
from a13n_service.tenancy.tables import PrincipalRow
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.anyio


def configuration(agent_id: str, *, state: str = "paused") -> dict:
    return {
        "name": "Daily report",
        "state": state,
        "schedule": {"type": "cron", "expression": "0 9 * * *", "timezone": "Asia/Shanghai"},
        "invocation": {
            "agent_id": agent_id,
            "agent_revision_id": None,
            "payload": {"content": [{"type": "text", "text": "Report today"}]},
        },
    }


async def test_creation_replay_and_memory_are_atomic(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    body = configuration(agent["id"])
    base = f"{service.api}/schedules"
    responses = await asyncio.gather(
        *[service.client.post(base, json=body, headers={"idempotency-key": "create-report"}) for _ in range(2)]
    )
    assert sorted(item.status_code for item in responses) == [200, 201], [item.text for item in responses]
    schedule = responses[0].json()
    assert responses[1].json() == schedule
    assert (
        schedule["state"] == "paused" and schedule["stream_url"] == f"/api/v1/sessions/{schedule['session_id']}/stream"
    )
    assert "authority" not in schedule and "create_request_key" not in schedule
    async with short_session(service.runtime.storage) as session:
        assert await session.scalar(select(func.count()).select_from(ScheduleRow)) == 1
        assert await session.scalar(select(func.count()).select_from(SessionRow)) == 1
        assert await session.scalar(select(func.count()).select_from(OccurrenceRow)) == 0
        assert await session.scalar(select(func.count()).select_from(ThreadRow)) == 0
        owner = await session.get_one(SessionRow, schedule["session_id"])
        assert owner.memory == {"enabled": False, "spaces": []}
    reused = await service.client.post(
        base, json={**body, "name": "Changed"}, headers={"idempotency-key": "create-report"}
    )
    assert reused.status_code == 409 and reused.json()["error"]["details"]["reason"] == "idempotency_key_reused"
    owner_url = f"{service.api}/sessions/{schedule['session_id']}"
    owner_view = (await service.client.get(owner_url)).json()
    memory = await service.client.patch(
        owner_url, json={"memory": {"enabled": True}}, headers=runs_kit.if_match(owner_view)
    )
    assert memory.status_code == 409 and memory.json()["error"]["details"]["reason"] == "schedule_managed_memory"
    labels = await service.client.patch(
        owner_url, json={"labels": {"team": "reports"}}, headers=runs_kit.if_match(owner_view)
    )
    assert labels.status_code == 200


async def test_versions_preview_and_archived_replay(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    base = f"{service.api}/schedules"
    body = configuration(agent["id"])
    created = await service.client.post(base, json=body, headers={"idempotency-key": "report"})
    assert created.status_code == 201, created.text
    schedule = created.json()
    url = f"{base}/{schedule['id']}"
    assert (await service.client.patch(url, json={"name": "Rename"})).status_code == 428
    no_change = await service.client.patch(url, json={"name": schedule["name"]}, headers=runs_kit.if_match(schedule))
    assert no_change.json()["version"] == schedule["version"]
    enabled = await service.client.post(f"{url}/enable", json={}, headers=runs_kit.if_match(schedule))
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["state"] == "enabled"
    progress = (await service.client.get(f"{url}/status")).json()
    assert progress["next_fire_at"] and progress["pending_occurrence_id"] is None
    assert progress["has_unfinished_work"] is False
    assert (await service.client.post(f"{url}/pause", json={}, headers=runs_kit.if_match(schedule))).status_code == 412
    changed = await service.client.patch(
        url, json={"description": "Updated"}, headers=runs_kit.if_match(enabled.json())
    )
    assert changed.status_code == 200, changed.text
    cleared = await service.client.patch(url, json={"description": None}, headers=runs_kit.if_match(changed.json()))
    assert cleared.json()["description"] is None
    invalid = await service.client.patch(url, json={"name": None}, headers=runs_kit.if_match(cleared.json()))
    assert invalid.status_code == 400
    preview = await service.client.post(
        f"{base}/preview", json={"schedule": body["schedule"], "after": "2026-10-19T01:00:00Z", "count": 2}
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["items"] == [
        {"utc": "2026-10-20T01:00:00Z", "local": "2026-10-20T09:00:00+08:00"},
        {"utc": "2026-10-21T01:00:00Z", "local": "2026-10-21T09:00:00+08:00"},
    ]
    assert (await service.client.delete(url, headers=runs_kit.if_match(cleared.json()))).status_code == 204
    archived = (await service.client.get(url)).json()
    assert archived["state"] == "archived"
    assert (await service.client.delete(url, headers=runs_kit.if_match(archived))).status_code == 204
    assert (await service.client.get(base)).json()["items"] == []
    assert len((await service.client.get(base, params={"state": "archived"})).json()["items"]) == 1
    replay = await service.client.post(base, json=body, headers={"idempotency-key": "report"})
    assert replay.status_code == 200 and replay.json()["session_id"] == schedule["session_id"]
    assert (await service.client.post(f"{url}/enable", json={}, headers=runs_kit.if_match(archived))).status_code == 409


async def test_input_validation_does_not_leave_sessions(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    base = f"{service.api}/schedules"
    body = configuration(agent["id"])
    for patch in [
        {"schedule": {"expression": "0 0 30 2 *", "timezone": "UTC"}},
        {"schedule": {"expression": "0 0 * * *", "timezone": "../UTC"}},
        {"invocation": {**body["invocation"], "payload": {"content": [{"type": "url", "url": "https://example.com"}]}}},
        {"session_id": new_object_id("sess")},
        {"invocation": {**body["invocation"], "agent_revision_id": new_object_id("apr")}},
    ]:
        response = await service.client.post(base, json={**body, **patch}, headers=runs_kit.fresh_key())
        assert response.status_code in {400, 404}, response.text
    async with short_session(service.runtime.storage) as session:
        assert await session.scalar(select(func.count()).select_from(ScheduleRow)) == 0
        assert await session.scalar(select(func.count()).select_from(SessionRow)) == 0


async def test_owner_and_delegation_ceiling(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    tenant = service.tenant

    def actor(identity: str, role: str) -> Principal:
        return Principal(identity, "user", (Grant(tenant.organization_id, tenant.workspace_id, BUILT_IN_ROLES[role]),))

    owner = actor(tenant.principal_id, "builder")
    request = ScheduleCreate.model_validate(configuration(agent["id"]))
    for role in ("runner", "viewer"):
        with pytest.raises(ServiceError) as error:
            await schedules.create(
                service.runtime, actor(tenant.principal_id, role), tenant.workspace_id, request, request_key=role
            )
        assert error.value.code == "forbidden"
    schedule, _ = await schedules.create(service.runtime, owner, tenant.workspace_id, request, request_key="owner")
    other_id = new_object_id("usr")
    async with transaction(service.runtime.storage) as session:
        session.add(PrincipalRow(id=other_id, kind="user", name="Other", email="other@example.com"))
    other = actor(other_id, "admin")
    for command in ("enable",):
        with pytest.raises(ServiceError) as error:
            await schedules.transition(
                service.runtime,
                other,
                tenant.workspace_id,
                schedule.id,
                command,
                if_match=f'"{schedule.id}:{schedule.version}"',
            )
        assert error.value.details["reason"] == "schedule_owner_required"
    with pytest.raises(ServiceError) as error:
        await schedules.update(
            service.runtime,
            other,
            tenant.workspace_id,
            schedule.id,
            ScheduleUpdate(name="Hijacked"),
            if_match=f'"{schedule.id}:{schedule.version}"',
        )
    assert error.value.details["reason"] == "schedule_owner_required"
    paused = await schedules.transition(
        service.runtime,
        other,
        tenant.workspace_id,
        schedule.id,
        "pause",
        if_match=f'"{schedule.id}:{schedule.version}"',
    )
    assert paused.state == "paused"


async def test_progress_does_not_change_etag_and_scope_is_enforced(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    created = await service.client.post(
        f"{service.api}/schedules", json=configuration(agent["id"], state="enabled"), headers=runs_kit.fresh_key()
    )
    assert created.status_code == 201, created.text
    schedule = created.json()
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"])
        row.next_fire_at += timedelta(days=1)
        await session.flush()
        assert (
            row.version == schedule["version"]
            and row.updated_at.isoformat().replace("+00:00", "Z") == schedule["updated_at"]
        )
    with pytest.raises(DBAPIError, match="schedule identity"):
        async with transaction(service.runtime.storage) as session:
            row = await session.get_one(ScheduleRow, schedule["id"])
            row.session_id = new_object_id("sess")
    other = await service.client.post(f"{service.organization}/workspaces", json={"name": "Other workspace"})
    assert other.status_code == 201, other.text
    response = await service.client.get(
        f"{service.api}/schedules/{schedule['id']}", headers={"x-workspace-id": other.json()["id"]}
    )
    assert response.status_code == 404


async def test_pause_records_elapsed_range_and_history_pages(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    # Only the pause command should consume the overdue position installed by this test.
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    base = f"{service.api}/schedules"
    created = await service.client.post(
        base, json=configuration(agent["id"], state="enabled"), headers=runs_kit.fresh_key()
    )
    assert created.status_code == 201, created.text
    schedule = created.json()
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"])
        current = await now(session)
        first = current.replace(hour=1, minute=0, second=0, microsecond=0) - timedelta(days=3)
        row.next_fire_at = first
    paused = await service.client.post(f"{base}/{schedule['id']}/pause", json={}, headers=runs_kit.if_match(schedule))
    assert paused.status_code == 200, paused.text
    page = await service.client.get(f"{base}/{schedule['id']}/occurrences", params={"limit": 1})
    assert page.status_code == 200, page.text
    item = page.json()["items"][0]
    assert item["status"] == "skipped" and item["reason"] == "schedule_paused"
    assert item["covered_from_at"] == first.isoformat().replace("+00:00", "Z")
    assert item["thread_id"] is None and item["entry_id"] is None and "snapshot" not in item
    detail = (await service.client.get(f"{base}/{schedule['id']}/occurrences/{item['id']}")).json()
    assert detail["snapshot"]["invocation"]["agent_id"] == agent["id"]
    assert detail["snapshot"]["timezone_data_version"]
    assert "authority" not in detail
    again = await service.client.post(
        f"{base}/{schedule['id']}/pause", json={}, headers=runs_kit.if_match(paused.json())
    )
    assert again.json()["version"] == paused.json()["version"]
    assert len((await service.client.get(f"{base}/{schedule['id']}/occurrences")).json()["items"]) == 1


async def test_list_filter_cursor_and_workspace_quota(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    base = f"{service.api}/schedules"
    for name in ("First", "Second", "Third"):
        created = await service.client.post(
            base,
            json={**configuration(agent["id"]), "name": name, "labels": {"team": "a"}},
            headers=runs_kit.fresh_key(),
        )
        assert created.status_code == 201, created.text
    page = (await service.client.get(base, params={"limit": 2})).json()
    next_page = (await service.client.get(base, params={"limit": 2, "cursor": page["next_cursor"]})).json()
    assert {item["name"] for item in [*page["items"], *next_page["items"]]} == {"First", "Second", "Third"}
    changed_filter = await service.client.get(base, params={"cursor": page["next_cursor"], "q": "First"})
    assert changed_filter.status_code == 400
    assert [item["name"] for item in (await service.client.get(base, params={"q": "first"})).json()["items"]] == [
        "First"
    ]
    settings = service.runtime.settings.model_copy(
        update={"schedules": service.runtime.settings.schedules.model_copy(update={"max_per_workspace": 3})}
    )
    runtime = replace(service.runtime, settings=settings)
    tenant = service.tenant
    actor = Principal(
        tenant.principal_id, "user", (Grant(tenant.organization_id, tenant.workspace_id, BUILT_IN_ROLES["admin"]),)
    )
    with pytest.raises(ServiceError) as error:
        await schedules.create(
            runtime,
            actor,
            tenant.workspace_id,
            ScheduleCreate.model_validate(configuration(agent["id"])),
            request_key="over-quota",
        )
    assert error.value.code == "rate_limited" and error.value.details["kind"] == "schedule_limit"
