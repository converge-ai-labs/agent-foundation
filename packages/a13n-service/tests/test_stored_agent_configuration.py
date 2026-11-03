"""Pre-cutover data remains inspectable while mutation and execution fail closed."""

import json

import pytest
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.schedules import dispatch
from a13n_service.runs.schedules.tables import OccurrenceRow
from sqlalchemy import text

from .test_schedule_dispatch import schedule_for, trigger

pytestmark = pytest.mark.anyio


async def test_historical_database_reads_and_executable_mutations(service, scripted_model, runs_kit):
    schedule, agent = await schedule_for(service, scripted_model, runs_kit)
    occurrence = await trigger(service, schedule, runs_kit)
    started = await runs_kit.start_thread(service, agent, "historical")
    run = started["run"]
    toolsets = {
        "agents": {
            "enabled": False,
            "config": {},
            "tools": {"create_agent_revision": {"enabled": False, "permission": "inherit", "config": {}}},
        }
    }
    options = {"overrides": {"toolsets": toolsets}}
    async with transaction(service.runtime.storage) as session:
        # Seed a genuine old immutable revision and frozen execution snapshots. No migration runs.
        await session.execute(text("SET LOCAL session_replication_role = replica"))
        await session.execute(
            text(
                "UPDATE agent_revisions SET config = jsonb_set(config, '{toolsets}', CAST(:value AS jsonb)) WHERE id = :id"
            ),
            {"value": json.dumps(toolsets), "id": agent["default_revision_id"]},
        )
        for table in ("runs", "inbox_entries"):
            await session.execute(
                text(f"UPDATE {table} SET options = CAST(:value AS jsonb)"), {"value": json.dumps(options)}
            )
        await session.execute(
            text(
                "UPDATE schedules SET invocation = jsonb_set(invocation, '{options}', CAST(:value AS jsonb)) WHERE id = :id"
            ),
            {"value": json.dumps(options), "id": schedule["id"]},
        )
        await session.execute(
            text(
                "UPDATE schedule_occurrences SET snapshot = jsonb_set(snapshot, '{invocation,options}', CAST(:value AS jsonb)) WHERE id = :id"
            ),
            {"value": json.dumps(options), "id": occurrence["id"]},
        )
    base = service.api
    paths = [
        f"/agents/{agent['id']}/revisions/{agent['default_revision_id']}",
        f"/agents/{agent['id']}/revisions",
        f"/threads/{run['thread_id']}/inbox",
        f"/runs/{run['id']}",
        f"/runs/{run['id']}/agent",
        f"/usage/context?run_id={run['id']}",
        f"/schedules/{schedule['id']}",
        "/schedules",
        f"/schedules/{schedule['id']}/occurrences/{occurrence['id']}",
    ]
    for path in paths:
        response = await service.client.get(base + path)
        assert response.status_code == 200, (path, response.text)
    frozen = (await service.client.get(f"{base}/runs/{run['id']}/agent")).json()
    assert "create_agent_revision" in frozen["config"]["toolsets"]["agents"]["tools"]
    for path, body in [
        (f"/agents/{agent['id']}/duplicate", {"name": "Forbidden copy"}),
        (f"/agents/{agent['id']}/revisions/{agent['default_revision_id']}/set-default", {}),
        (f"/schedules/{schedule['id']}/enable", {}),
    ]:
        response = await service.client.post(
            base + path, json=body, headers=runs_kit.if_match(schedule if "schedules" in path else agent)
        )
        assert response.status_code == 409, (path, response.text)
        assert response.json()["error"]["details"]["reason"] == "configuration_unavailable"
    child = await service.client.post(
        f"{base}/agents",
        json={
            "name": "Pinned child",
            "config": {
                "model": frozen["config"]["model"],
                "subagents": {"child": {"agent_id": agent["id"], "revision_id": agent["default_revision_id"]}},
            },
        },
    )
    assert child.status_code == 409, child.text
    assert child.json()["error"]["details"]["reason"] == "configuration_unavailable"
    changed = await service.client.patch(
        f"{base}/schedules/{schedule['id']}", json={"name": "Updated"}, headers=runs_kit.if_match(schedule)
    )
    assert changed.status_code == 409, changed.text
    # A persisted occurrence is diagnosed through the existing lifecycle instead of crashing its sweep.
    await dispatch.process(service.runtime, schedule["id"])
    async with short_session(service.runtime.storage) as session:
        item = await session.get_one(OccurrenceRow, occurrence["id"])
        assert item.status == "failed"


async def test_persisted_reviewer_override_is_readable_but_execution_is_classified(service, scripted_model, runs_kit):
    from a13n_service.resources.agents import service as agents
    from a13n_service.resources.agents import stored
    from a13n_service.runs import agent as execution
    from a13n_service.runs.schemas import RunOptions
    from a13n_service.runs.tables import RunRow
    from a13n_service.tenancy.access import WorkspaceScope
    from a13n_service.tenancy.authorize import ExecutionAuthority

    from .test_agent_configuration_changes import actor

    await runs_kit.pause_sweeps(service)
    head = await runs_kit.create_agent(service, scripted_model)
    run = (await runs_kit.start_thread(service, head, "old reviewer"))["run"]
    async with transaction(service.runtime.storage) as session:
        await session.execute(text("SET LOCAL session_replication_role = replica"))
        row = await session.get_one(RunRow, run["id"])
        revision = await agents.load_revision(session, row.agent_id, row.agent_revision_id)
        row.options = {
            "overrides": {
                "reviewer": {
                    "model": revision.config.model,
                    "rules": {"mcp/platform/list_agents": {"on_flagged": "approval_required"}},
                }
            }
        }
    response = await service.client.get(f"{service.api}/runs/{run['id']}/agent")
    assert response.status_code == 200, response.text
    assert "mcp/platform/list_agents" in response.json()["config"]["reviewer"]["rules"]
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(RunRow, run["id"])
        options = stored.require(RunOptions, row.options, kind="run", id=row.id)
        with pytest.raises(ServiceError) as refused:
            await execution.resolve(
                session,
                actor(service),
                WorkspaceScope(row.organization_id, row.workspace_id),
                revision,
                authority=ExecutionAuthority.model_validate(row.authority),
                override=options.overrides,
                registry=service.runtime.registry,
            )
        assert refused.value.code == "conflict"
        assert refused.value.details["reason"] == "configuration_unavailable"


async def test_obsolete_discovery_snapshots_return_configuration_conflicts(service, scripted_model, runs_kit):
    schedule, agent = await schedule_for(service, scripted_model, runs_kit)
    occurrence = await trigger(service, schedule, runs_kit)
    run = (await runs_kit.start_thread(service, agent, "obsolete discovery"))["run"]
    legacy = {"groups": {"legacy": {"description": "Environment tools", "capabilities": ["environment"]}}}
    options = {"overrides": {"tool_search": legacy}}
    async with transaction(service.runtime.storage) as session:
        # Historical immutable rows can contain the former selector. Reading must not rewrite them.
        await session.execute(text("SET LOCAL session_replication_role = replica"))
        await session.execute(
            text(
                "UPDATE agent_revisions SET config = jsonb_set(config, '{tool_search}', CAST(:value AS jsonb)) WHERE id = :id"
            ),
            {"value": json.dumps(legacy), "id": agent["default_revision_id"]},
        )
        await session.execute(
            text("UPDATE runs SET options = CAST(:value AS jsonb) WHERE id = :id"),
            {"value": json.dumps(options), "id": run["id"]},
        )
        await session.execute(
            text("UPDATE inbox_entries SET options = CAST(:value AS jsonb) WHERE thread_id = :id"),
            {"value": json.dumps(options), "id": run["thread_id"]},
        )
        await session.execute(
            text(
                "UPDATE schedules SET invocation = jsonb_set(invocation, '{options}', CAST(:value AS jsonb)) WHERE id = :id"
            ),
            {"value": json.dumps(options), "id": schedule["id"]},
        )
        await session.execute(
            text(
                "UPDATE schedule_occurrences SET snapshot = jsonb_set(snapshot, '{invocation,options}', CAST(:value AS jsonb)) WHERE id = :id"
            ),
            {"value": json.dumps(options), "id": occurrence["id"]},
        )
    for path, kind, id in [
        (
            f"/agents/{agent['id']}/revisions/{agent['default_revision_id']}",
            "agent_revision",
            agent["default_revision_id"],
        ),
        (f"/agents/{agent['id']}/revisions", "agent_revision", agent["default_revision_id"]),
        (f"/runs/{run['id']}", "run", run["id"]),
        (f"/runs/{run['id']}/agent", "agent_revision", agent["default_revision_id"]),
        (f"/usage/context?run_id={run['id']}", "agent_revision", agent["default_revision_id"]),
        (f"/schedules/{schedule['id']}", "schedule", schedule["id"]),
        ("/schedules", "schedule", schedule["id"]),
        (f"/schedules/{schedule['id']}/occurrences/{occurrence['id']}", "schedule_occurrence", occurrence["id"]),
    ]:
        response = await service.client.get(service.api + path)
        assert response.status_code == 409, (path, response.text)
        error = response.json()["error"]
        assert error["code"] == "conflict"
        assert error["details"] == {"kind": kind, "id": id, "reason": "configuration_unavailable"}
        assert "capabilities" not in response.text
    inbox = await service.client.get(f"{service.api}/threads/{run['thread_id']}/inbox")
    assert inbox.status_code == 409
    assert inbox.json()["error"]["details"]["kind"] == "inbox_entry"
    async with short_session(service.runtime.storage) as session:
        saved = await session.scalar(
            text("SELECT config->'tool_search' FROM agent_revisions WHERE id = :id"),
            {"id": agent["default_revision_id"]},
        )
    assert saved == legacy
