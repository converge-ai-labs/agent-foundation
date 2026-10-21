"""Schedule tools preserve HTTP semantics, Harness review and durable execution authority."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.resources.agents.composer import configuration as composer_configuration
from a13n_service.resources.agents.toolsets import catalog, platform_tools
from a13n_service.runs.claim import claim
from a13n_service.runs.platform_tokens import issue
from a13n_service.runs.schedules import dispatch
from a13n_service.runs.schedules.sweep import plan_next
from a13n_service.runs.schedules.tables import OccurrenceRow, ScheduleRow
from a13n_service.runs.tables import InboxEntryRow, RunRow
from a13n_service.tenancy.authorize import BUILT_IN_ROLES
from sqlalchemy import select

from .mcp_support import call, client, key, rpc
from .test_platform_tools import lease_for, set_role
from .test_schedules import configuration

pytestmark = pytest.mark.anyio

READS = {
    "list_schedules",
    "get_schedule",
    "preview_schedule",
    "get_schedule_status",
    "list_schedule_occurrences",
    "get_schedule_occurrence",
}
WRITES = {
    "create_schedule",
    "update_schedule",
    "enable_schedule",
    "pause_schedule",
    "archive_schedule",
    "dispatch_schedule",
}


def rule(kind):
    if kind == "once":
        return {"type": "once", "run_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()}
    return {"type": "cron", "expression": "* * * * *", "timezone": "Asia/Shanghai"}


async def test_schedule_discovery_selection_and_group_scope(service, scripted_model, runs_kit):
    lease, _, _ = await lease_for(service, scripted_model, runs_kit)
    token = await issue(service.runtime, lease, frozenset({"schedules"}))
    async with client(service, {"secret": token}) as http:
        tools = (await rpc(http, "tools/list")).json()["result"]["tools"]
        assert {tool["name"] for tool in tools} == READS | WRITES
        for tool in tools:
            read = tool["name"] in READS
            assert tool["_meta"]["x-a13n-platform"] == {"group": "schedules", "effect": "read" if read else "write"}
            assert tool["annotations"]["readOnlyHint"] is read
        http.headers["x-workspace-id"] = "ws_another"
        assert (await rpc(http, "tools/list")).status_code == 403
        del http.headers["x-workspace-id"]
        assert (await http.post(f"/api/v1/runs/{lease.run_id}/interrupt")).status_code == 403

    group = next(item for item in catalog(frozenset()).items if item.key == "schedules")
    assert group.platform and not group.default_enabled
    assert {tool.key: tool.default_permission for tool in group.tools} == {
        **dict.fromkeys(READS, "allow"),
        **dict.fromkeys(WRITES, "ask"),
    }
    assert READS | WRITES <= platform_tools(composer_configuration("model").toolsets).keys()

    token = await issue(service.runtime, lease, frozenset({"agents"}))
    async with client(service, {"secret": token}) as http:
        tools = (await rpc(http, "tools/list")).json()["result"]["tools"]
        assert not (READS | WRITES) & {tool["name"] for tool in tools}
        assert (await rpc(http, "tools/call", name="list_schedules", arguments={})).json()["result"]["isError"]
        assert (await http.get("/api/v1/schedules")).status_code == 403


@pytest.mark.parametrize(("kind", "credential_kind"), [("cron", "run"), ("once", "api_key")])
async def test_schedule_mcp_management(service, scripted_model, runs_kit, kind, credential_kind):
    await runs_kit.pause_sweeps(service)
    lease, agent, _ = await lease_for(service, scripted_model, runs_kit)
    credential = (
        {"secret": await issue(service.runtime, lease, frozenset({"schedules"}))}
        if credential_kind == "run"
        else await key(service)
    )
    body = {**configuration(agent["id"]), "schedule": rule(kind)}
    async with client(service, credential) as http:
        preview = await call(
            http,
            service,
            "POST",
            "/schedules/preview",
            request_body={
                "schedule": body["schedule"],
                "after": datetime.now(UTC).isoformat(),
                "count": 2,
            },
        )
        assert preview["status"] == 200 and len(preview["body"]["items"]) == (1 if kind == "once" else 2)
        create_args = {"Idempotency-Key": "schedule-create", "request_body": body}
        created = await call(http, service, "POST", "/schedules", **create_args)
        assert created["status"] == 201, created
        schedule = created["body"]
        assert schedule["state"] == "paused"
        replay = await call(http, service, "POST", "/schedules", **create_args)
        assert replay["status"] == 200 and replay["body"]["id"] == schedule["id"]
        reused = await call(
            http,
            service,
            "POST",
            "/schedules",
            **{
                **create_args,
                "request_body": {**body, "name": "Different intent"},
            },
        )
        assert reused["status"] == 409
        assert reused["body"]["error"]["details"]["reason"] == "idempotency_key_reused"
        page = await call(http, service, "GET", "/schedules", q="daily", state="paused", limit=1)
        assert [item["id"] for item in page["body"]["items"]] == [schedule["id"]]
        args = {"schedule_id": schedule["id"]}
        read = await call(http, service, "GET", "/schedules/{schedule_id}", **args)
        assert read["headers"]["etag"] == created["headers"]["etag"]
        missing = await call(
            http, service, "PATCH", "/schedules/{schedule_id}", **args, request_body={"name": "Renamed"}
        )
        assert missing["status"] == 428
        changed = await call(
            http,
            service,
            "PATCH",
            "/schedules/{schedule_id}",
            **args,
            **{"If-Match": read["headers"]["etag"]},
            request_body={"name": "Renamed"},
        )
        assert changed["status"] == 200 and changed["body"]["name"] == "Renamed"
        stale = await call(
            http,
            service,
            "POST",
            "/schedules/{schedule_id}/enable",
            **args,
            **{"If-Match": read["headers"]["etag"]},
            request_body={},
        )
        assert stale["status"] == 412
        enabled = await call(
            http,
            service,
            "POST",
            "/schedules/{schedule_id}/enable",
            **args,
            **{"If-Match": changed["headers"]["etag"]},
            request_body={},
        )
        assert enabled["status"] == 200 and enabled["body"]["state"] == "enabled"
        status = await call(http, service, "GET", "/schedules/{schedule_id}/status", **args)
        assert status["body"]["next_fire_at"] and not status["body"]["has_unfinished_work"]
        paused = await call(
            http,
            service,
            "POST",
            "/schedules/{schedule_id}/pause",
            **args,
            **{"If-Match": enabled["headers"]["etag"]},
            request_body={},
        )
        assert paused["status"] == 200 and paused["body"]["state"] == "paused"
        dispatch_args = {
            **args,
            "If-Match": paused["headers"]["etag"],
            "Idempotency-Key": "schedule-dispatch",
            "request_body": {},
        }
        triggered = await call(http, service, "POST", "/schedules/{schedule_id}/dispatch", **dispatch_args)
        assert triggered["status"] == 202 and triggered["body"]["status"] == "pending"
        replay = await call(http, service, "POST", "/schedules/{schedule_id}/dispatch", **dispatch_args)
        assert replay["status"] == 200 and replay["body"]["id"] == triggered["body"]["id"]
        history = await call(
            http, service, "GET", "/schedules/{schedule_id}/occurrences", **args, include="execution", limit=1
        )
        assert [item["id"] for item in history["body"]["items"]] == [triggered["body"]["id"]]
        occurrence = await call(
            http,
            service,
            "GET",
            "/schedules/{schedule_id}/occurrences/{occurrence_id}",
            **args,
            occurrence_id=triggered["body"]["id"],
            include="execution",
        )
        assert occurrence["body"]["snapshot"]["invocation"] == schedule["invocation"]
        assert occurrence["body"]["execution"]["initial_run"] is None
        archived = await call(
            http, service, "DELETE", "/schedules/{schedule_id}", **args, **{"If-Match": paused["headers"]["etag"]}
        )
        assert archived["status"] == 204 and archived["body"] is None
        replay = await call(http, service, "POST", "/schedules/{schedule_id}/dispatch", **dispatch_args)
        assert replay["status"] == 200 and replay["body"]["status"] == "cancelled"


async def test_schedule_tools_cannot_expand_run_permissions(service, scripted_model, runs_kit):
    agent = await runs_kit.create_agent(service, scripted_model)
    async with transaction(service.runtime.storage) as session:
        await set_role(session, service, "runner")
    await runs_kit.start_thread(service, agent, "Read schedules")
    (lease,) = await claim(service.runtime, worker_id="schedule-test", worker_build="test", limit=1)
    token = await issue(service.runtime, lease, frozenset({"schedules"}))
    async with transaction(service.runtime.storage) as session:
        await set_role(session, service, "admin")
    async with client(service, {"secret": token}) as http:
        assert (await call(http, service, "GET", "/schedules"))["status"] == 200
        result = await call(
            http,
            service,
            "POST",
            "/schedules",
            **{"Idempotency-Key": "forbidden"},
            request_body=configuration(agent["id"]),
        )
        assert result["status"] == 403
    async with short_session(service.runtime.storage) as session:
        assert (await session.scalars(select(ScheduleRow))).all() == []


@pytest.mark.parametrize(("kind", "revoked"), [("cron", False), ("once", False), ("cron", True)])
async def test_composer_reviews_durable_schedule_and_preserves_authority(
    service, scripted_model, runs_kit, listen, kind, revoked
):
    await runs_kit.pause_sweeps(service)
    target = await runs_kit.create_agent(service, scripted_model)
    prepared = await service.client.post("/api/v1/agent-composer")
    assert prepared.status_code == 200, prepared.text
    composer = prepared.json()
    body = {**configuration(target["id"], state="enabled"), "schedule": rule(kind)}
    scripted_model.call(
        "view", {"file_path": "/platform-skills/platform-operations/references/schedules.md"}, call_id="guide"
    )
    scripted_model.call(
        "preview_schedule",
        {
            "request_body": {
                "schedule": body["schedule"],
                "after": datetime.now(UTC).isoformat(),
                "count": 1,
            }
        },
        call_id="preview",
    )
    scripted_model.call(
        "create_schedule", {"Idempotency-Key": "reviewed-schedule", "request_body": body}, call_id="create"
    )
    async with transaction(service.runtime.storage) as session:
        await set_role(session, service, "builder")
    async with listen(service.app) as url:
        settings = service.runtime.settings
        runtime = replace(
            service.runtime,
            settings=settings.model_copy(update={"server": settings.server.model_copy(update={"control_url": url})}),
        )
        submitted = await runs_kit.start_thread(service, composer, "Schedule this report and enable it")
        await (await runs_kit.attempt(service, runtime=runtime))
        run = await runs_kit.get_run(service, submitted["run"]["id"])
        assert run["status"] == "waiting", run
        async with short_session(service.runtime.storage) as session:
            assert (await session.scalars(select(ScheduleRow))).all() == []
        # New grants cannot widen the saved Run ceiling, including after approval/resume.
        async with transaction(service.runtime.storage) as session:
            await set_role(session, service, "admin")
        scripted_model.say("Schedule saved")
        resumed = await service.client.post(
            f"/api/v1/runs/{run['id']}/resume",
            json={"approvals": {"create": {"action": "approve"}}, "calls": {}},
            headers=runs_kit.fresh_key(),
        )
        assert resumed.status_code == 201, resumed.text
        await (await runs_kit.attempt(service, runtime=runtime))
        assert (await runs_kit.get_run(service, resumed.json()["id"]))["status"] == "completed"

    async with transaction(service.runtime.storage) as session:
        row = (await session.scalars(select(ScheduleRow))).one()
        schedule_id = row.id
        assert row.created_by_id == row.principal_id == service.tenant.principal_id
        assert set(row.authority["verbs"]) == BUILT_IN_ROLES["builder"]
        assert row.state == "enabled"
        event = (
            await session.scalars(
                select(AuditEventRow).where(
                    AuditEventRow.target_id == row.id, AuditEventRow.action == "schedule.create"
                )
            )
        ).one()
        assert event.details["execution_origin"]["run_id"] == resumed.json()["id"]
        # Advance the persisted test schedule to a due instant without wall-clock sleeps.
        due = (await now(session)).replace(second=0, microsecond=0) - timedelta(minutes=1)
        row.next_fire_at = due
        if kind == "once":
            row.run_at = due
        if revoked:
            await set_role(session, service, "viewer")
    assert await plan_next(service.runtime) == schedule_id
    await dispatch.process(service.runtime, schedule_id)
    async with short_session(service.runtime.storage) as session:
        occurrence = (await session.scalars(select(OccurrenceRow))).one()
        if revoked:
            assert occurrence.status == "failed" and occurrence.reason == "authority_invalid"
            assert occurrence.entry_id is None
            assert (await session.get_one(ScheduleRow, schedule_id)).state == "paused"
        else:
            assert occurrence.status == "submitted" and occurrence.entry_id
            entry = await session.get_one(InboxEntryRow, occurrence.entry_id)
            scheduled_run = await session.get_one(RunRow, entry.assigned_run_id)
            assert set(scheduled_run.authority["verbs"]) == BUILT_IN_ROLES["builder"]
