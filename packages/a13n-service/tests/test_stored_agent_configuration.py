"""Pre-cutover data remains inspectable while mutation and execution fail closed."""

import json

import pytest
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.schedules import dispatch
from a13n_service.runs.schedules.tables import OccurrenceRow
from sqlalchemy import select, text

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
                "subagents": {
                    "mode": "inline",
                    "named": {"child": {"agent_id": agent["id"], "revision_id": agent["default_revision_id"]}},
                },
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
    healthy_schedule, healthy_agent = await schedule_for(service, scripted_model, runs_kit)
    agent = healthy_agent
    response = await service.client.post(
        f"{service.api}/schedules",
        json={
            "name": "Obsolete schedule",
            "schedule": healthy_schedule["schedule"],
            "invocation": healthy_schedule["invocation"],
        },
        headers=runs_kit.fresh_key(),
    )
    assert response.status_code == 201, response.text
    schedule = response.json()
    occurrence = await trigger(service, schedule, runs_kit)
    started = await runs_kit.start_thread(service, agent, "obsolete discovery")
    run = started["run"]
    healthy = await runs_kit.start_thread(
        service, healthy_agent, "same session", session_id=started["thread"]["session_id"]
    )
    independent = await runs_kit.start_thread(service, healthy_agent, "another session")
    entry = await runs_kit.submit(service, run["thread_id"], runs_kit.message(healthy_agent, "healthy entry"))
    assert entry.status_code == 201, entry.text
    entries = await runs_kit.inbox(service, run["thread_id"])
    healthy_entry = entries[-1]["id"]
    bad_entry = entries[0]["id"]
    revision = (
        await service.client.get(f"{service.api}/agents/{agent['id']}/revisions/{agent['default_revision_id']}")
    ).json()
    revision["config"]["instructions"] = "New healthy revision"
    published = await service.client.post(
        f"{service.api}/agents/{agent['id']}/revisions",
        json={"config": revision["config"], "make_default": False},
        headers=runs_kit.if_match(agent),
    )
    assert published.status_code == 201, published.text
    healthy_revision = published.json()["id"]

    from a13n_service.runs.schemas import Failure, Outcome
    from a13n_service.runs.seal import seal
    from a13n_service.runs.session_state import Scope
    from a13n_service.runs.stream import SessionHub
    from a13n_service.runs.tables import RunRow, ThreadRow

    hub = SessionHub(service.runtime)
    scopes = [Scope(run["workspace_id"], item["thread"]["session_id"]) for item in (started, independent)]
    watched = [await hub._watch(scope) for scope in scopes]
    old_keys = {id: watch.key for state in watched for id, watch in state.threads.items()}
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
            text("UPDATE inbox_entries SET options = CAST(:value AS jsonb) WHERE id = :id"),
            {"value": json.dumps(options), "id": bad_entry},
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
        (f"/runs/{run['id']}", "run", run["id"]),
        (f"/runs/{run['id']}/agent", "agent_revision", agent["default_revision_id"]),
        (f"/usage/context?run_id={run['id']}", "agent_revision", agent["default_revision_id"]),
        (f"/schedules/{schedule['id']}", "schedule", schedule["id"]),
        (f"/schedules/{schedule['id']}/occurrences/{occurrence['id']}", "schedule_occurrence", occurrence["id"]),
    ]:
        response = await service.client.get(service.api + path)
        assert response.status_code == 409, (path, response.text)
        error = response.json()["error"]
        assert error["code"] == "conflict"
        assert error["details"] == {"kind": kind, "id": id, "reason": "configuration_unavailable"}
        assert "capabilities" not in response.text
    # A marker consumes a page position, including an otherwise empty page. Healthy neighbors remain visible.
    for path, kind, bad, good, views in [
        ("/schedules", "schedule", schedule["id"], healthy_schedule["id"], (None,)),
        (
            f"/agents/{agent['id']}/revisions",
            "agent_revision",
            agent["default_revision_id"],
            healthy_revision,
            ("detailed", "compact"),
        ),
        (f"/threads/{run['thread_id']}/inbox", "inbox_entry", bad_entry, healthy_entry, ("detailed", "compact")),
    ]:
        for view in views:
            params = {"limit": 1, **({"view": view} if view else {})}
            seen, unavailable = [], []
            for _ in range(4):
                page = await service.client.get(service.api + path, params=params)
                assert page.status_code == 200, page.text
                data = page.json()
                seen.extend(item["id"] for item in data["items"])
                unavailable.extend(data["unavailable"])
                if data["next_cursor"] is None:
                    break
                params["cursor"] = data["next_cursor"]
            else:
                pytest.fail("Pagination did not progress past an unavailable record")
            assert good in seen and bad not in seen
            assert unavailable == [{"kind": kind, "id": bad, "reason": "configuration_unavailable"}]

    # One refresh spans both sessions; bad current and last runs cannot block any healthy thread.
    for last_run in (False, True):
        async with transaction(service.runtime.storage) as session:
            await session.execute(text("UPDATE threads SET version = version + 1"))
            if last_run:
                thread_row = await session.scalar(
                    select(ThreadRow).where(ThreadRow.id == run["thread_id"]).with_for_update()
                )
                run_row = await session.scalar(select(RunRow).where(RunRow.id == run["id"]).with_for_update())
                assert thread_row is not None and run_row is not None
                await seal(
                    session,
                    service.runtime,
                    thread_row,
                    run_row,
                    None,
                    Outcome(
                        status="failed",
                        failure=Failure(code="configuration_unavailable", message="Operator repair required"),
                    ),
                    at=await now(session),
                )
        await hub._refresh_once()
        for state in watched:
            for id, watch in state.threads.items():
                assert watch.key != old_keys[id]
                old_keys[id] = watch.key
        bad_state = json.loads(watched[0].threads[run["thread_id"]].frame.split("data: ", 1)[1])
        assert {item["id"] for item in bad_state["unavailable"]} >= {run["id"]}
        assert bad_state["current_run"] is None
        assert any(item["id"] == healthy_entry for item in bad_state["entries"])
        for state, item in ((watched[0], healthy), (watched[1], independent)):
            frame = json.loads(state.threads[item["thread"]["id"]].frame.split("data: ", 1)[1])
            assert frame["current_run"]["id"] == item["run"]["id"]
            assert frame["unavailable"] == []
    fresh = await SessionHub(service.runtime)._watch(scopes[0])
    assert set(fresh.threads) == set(watched[0].threads)
    async with short_session(service.runtime.storage) as session:
        saved = await session.scalar(
            text("SELECT config->'tool_search' FROM agent_revisions WHERE id = :id"),
            {"id": agent["default_revision_id"]},
        )
    assert saved == legacy


def test_batch_reads_do_not_hide_authorization_or_infrastructure_errors():
    from a13n_service.resources.agents import stored

    for code in ("forbidden", "unavailable", "conflict"):
        error = ServiceError(code, "unrelated error")

        def fail(error=error):
            raise error

        with pytest.raises(ServiceError) as caught:
            stored.batch_item(fail)
        assert caught.value is error
