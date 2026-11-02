"""Task tools construct invocation and preserve authoritative Schedule lifecycle semantics."""

from types import SimpleNamespace

import pytest
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import etag
from a13n_service.platform_tools import ToolContext
from a13n_service.runs.schedules import platform_tools as tools
from a13n_service.runs.schedules.schemas import CronSchedule, OccurrenceQuery
from a13n_service.runs.schedules.tasks import TaskChanges, TaskOptions
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal
from fastapi import Request
from pydantic import ValidationError

pytestmark = pytest.mark.anyio


def context(service: SimpleNamespace) -> ToolContext:
    tenant = service.tenant
    actor = Principal(tenant.principal_id, "user", (Grant(tenant.organization_id, None, BUILT_IN_ROLES["admin"]),))
    return ToolContext(service.runtime, actor, tenant.workspace_id, Request({"type": "http", "app": service.app}), None)


def test_task_contract_requires_versions_and_owns_invocation():
    contracts = {value.name: value for value in tools.TOOLS}
    assert len(contracts) == 12
    creation = contracts["create_schedule"].inputs.model_json_schema()
    assert "invocation" not in creation["properties"] and "request_body" not in creation["properties"]
    assert {"instructions", "agent_id", "schedule", "idempotency_key"} <= set(creation["required"])
    for name in ("update_schedule", "enable_schedule", "pause_schedule", "archive_schedule", "dispatch_schedule"):
        assert "expected_version" in contracts[name].inputs.model_json_schema()["required"]
    with pytest.raises(ValidationError):
        TaskChanges(instructions=None)
    with pytest.raises(ValidationError):
        TaskChanges(structured_inputs=None)
    assert TaskChanges(description=None).model_fields_set == {"description"}


async def test_task_edits_and_dispatch_keep_input_options_and_snapshot(service, scripted_model, runs_kit):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    ctx = context(service)
    args = dict(
        name="Report",
        instructions="Summarize changes",
        agent_id=agent["id"],
        schedule=CronSchedule(expression="0 9 * * *", timezone="Asia/Taipei"),
        idempotency_key="task-create",
        structured_inputs=({"include": ["tests", "docs"], "optional": None},),
        options=TaskOptions(labels={"kind": "daily"}, max_usage={"requests": 4}),
    )
    created = await tools.create_schedule(ctx, **args)
    assert created.state == "paused"
    replay = await tools.create_schedule(ctx, **args)
    assert replay.id == created.id and replay.version == created.version
    changed = await tools.update_schedule(
        ctx,
        schedule_id=created.id,
        expected_version=etag(created.id, created.version),
        body=TaskChanges(instructions="Summarize and verify"),
    )
    assert changed.invocation.payload.text() == "Summarize and verify"
    assert changed.invocation.payload.content[1] == created.invocation.payload.content[1]
    assert changed.invocation.options == created.invocation.options
    with pytest.raises(ServiceError) as stale:
        await tools.update_schedule(
            ctx,
            schedule_id=created.id,
            expected_version=etag(created.id, created.version),
            body=TaskChanges(description="Do not apply"),
        )
    assert stale.value.code == "precondition_failed"
    occurrence = await tools.dispatch_schedule(
        ctx, schedule_id=changed.id, expected_version=etag(changed.id, changed.version), idempotency_key="manual-report"
    )
    assert occurrence.snapshot.invocation == changed.invocation
    again = await tools.dispatch_schedule(
        ctx, schedule_id=changed.id, expected_version=etag(changed.id, changed.version), idempotency_key="manual-report"
    )
    assert again.id == occurrence.id
    edited = await tools.update_schedule(
        ctx,
        schedule_id=changed.id,
        expected_version=etag(changed.id, changed.version),
        body=TaskChanges(name="Renamed"),
    )
    assert edited.invocation == changed.invocation
    retained = await tools.get_schedule_occurrence(ctx, schedule_id=changed.id, occurrence_id=occurrence.id)
    assert retained.snapshot.name == "Report"
    assert retained.snapshot.invocation == changed.invocation
    history = await tools.list_schedule_occurrences(ctx, schedule_id=changed.id, filters=OccurrenceQuery())
    assert [item.id for item in history.items] == [occurrence.id]
    enabled = await tools.enable_schedule(ctx, schedule_id=edited.id, expected_version=etag(edited.id, edited.version))
    assert enabled.state == "enabled"
    paused = await tools.pause_schedule(ctx, schedule_id=enabled.id, expected_version=etag(enabled.id, enabled.version))
    assert paused.state == "paused"
    archived = await tools.archive_schedule(
        ctx, schedule_id=paused.id, expected_version=etag(paused.id, paused.version)
    )
    assert archived.state == "archived"
