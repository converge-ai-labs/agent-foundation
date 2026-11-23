"""Installed-App authority and native Service transactions against PostgreSQL and Redis."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from a13n_service.app_plugins import AppCatalog, AppManifest
from a13n_service.app_sdk import agents, egress, projects, schedules, threads, usage
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.egress_credentials.notifications import CHANNEL
from a13n_service.runs.schedules.schemas import ScheduleCreate
from a13n_service.runs.schemas import SessionCreate
from a13n_service.runs.tables import ThreadRow
from a13n_service.tenancy.tables import OrganizationRow
from sqlalchemy import select

pytestmark = pytest.mark.anyio


@pytest.fixture
async def app_runtime(service):
    runtime = replace(
        service.runtime,
        apps=AppCatalog(tuple(AppManifest(app_id, app_id, app_id, "1") for app_id in ("claw", "other", "disabled"))),
    )
    async with transaction(runtime.storage) as session:
        org = await session.get_one(OrganizationRow, service.tenant.organization_id)
        org.settings = {**org.settings, "app_plugins": {"enabled": ["claw", "other"]}}
    return runtime


@pytest.fixture
async def runner(service):
    response = await service.client.post(f"{service.workspace}/service-accounts", json={"name": "app-runner"})
    assert response.status_code == 201 and response.json()["role"] == "runner", response.text
    return response.json()["id"]


async def test_egress_app_ownership_and_live_version(service, app_runtime, runner):
    runtime, ws = app_runtime, service.tenant.workspace_id
    created = await egress.create_app_credential(
        runtime,
        ws,
        app_id="claw",
        actor_id=runner,
        name="App token",
        allowed_hosts=["example.com"],
        value="old",
    )
    assert created.owner_app_id == "claw" and created.created_by_id == runner
    path = f"{service.api}/egress-credentials/{created.id}"
    read = await service.client.get(path)
    assert read.json()["owner_app_id"] == "claw"
    assert (await service.client.get(f"{service.api}/egress-credentials")).json()["items"][0]["id"] == created.id
    headers = {"if-match": read.headers["etag"]}
    for method, suffix, body in [
        ("PATCH", "", {"name": "human"}),
        ("PUT", "/value", {"value": "human"}),
        ("POST", "/revoke", None),
    ]:
        refused = await service.client.request(method, path + suffix, headers=headers, json=body)
        assert refused.status_code == 409 and "app_managed_credential" in refused.text
    assert (await service.client.post(f"{path}/sync")).status_code == 202
    owner = {"app_id": "claw", "actor_id": runner, "credential_id": created.id}
    async with runtime.redis.pubsub() as subscriber:
        await subscriber.subscribe(CHANNEL)
        assert (await subscriber.get_message(timeout=2))["type"] == "subscribe"
        rotated = await egress.rotate_app_credential(runtime, ws, **owner, value="new")
        assert rotated.credential_version == 2 and rotated.id == created.id and rotated.updated_by_id == runner
        event = await subscriber.get_message(timeout=2)
        assert event is not None and json.loads(event["data"]) == {"credential_id": created.id, "version": 2}
        revoked = await egress.revoke_app_credential(runtime, ws, **owner)
        assert revoked.state == "revoked" and revoked.credential_version == 3
        event = await subscriber.get_message(timeout=2)
        assert event is not None and json.loads(event["data"])["version"] == 3
    async with short_session(runtime.storage) as session:
        events = (
            await session.scalars(
                select(AuditEventRow).where(
                    AuditEventRow.target_id == created.id,
                    AuditEventRow.action.in_(
                        ("egress_credential.create", "egress_credential.rotate", "egress_credential.revoke")
                    ),
                )
            )
        ).all()
        assert len(events) == 3 and all(
            event.actor_id == runner and event.details["app_id"] == "claw" for event in events
        )


async def test_egress_isolation(service, app_runtime, runner):
    runtime, tenant = app_runtime, service.tenant
    ws = tenant.workspace_id
    created = await egress.create_app_credential(
        runtime,
        ws,
        app_id="claw",
        actor_id=runner,
        name="App token",
        allowed_hosts=["example.com"],
        value="token",
    )
    assert await egress.get_app_credential(runtime, ws, app_id="other", credential_id=created.id) is None
    with pytest.raises(ServiceError) as disabled:
        await egress.get_app_credential(runtime, ws, app_id="disabled", credential_id=created.id)
    assert disabled.value.code == "disabled"
    with pytest.raises(ServiceError) as not_installed:
        await egress.revoke_app_credential(runtime, ws, app_id="unknown", actor_id=runner, credential_id=created.id)
    assert not_installed.value.code == "not_found"
    human = await service.client.post(
        f"{service.api}/egress-credentials",
        json={
            "name": "Human token",
            "allowed_hosts": ["example.org"],
            "value": "token",
        },
    )
    assert human.status_code == 201
    other = await service.client.post(f"{service.organization}/workspaces", json={"name": "Other"})
    assert other.status_code == 201, other.text
    for target_ws, app_id, target_id in [
        (ws, "other", created.id),
        (ws, "claw", human.json()["id"]),
        (other.json()["id"], "claw", created.id),
    ]:
        with pytest.raises(ServiceError) as denied:
            await egress.rotate_app_credential(
                runtime,
                target_ws,
                app_id=app_id,
                actor_id=tenant.principal_id,
                credential_id=target_id,
                value="different",
            )
        assert denied.value.code == "not_found"


async def test_native_thread_create_replay_submit_rollback_and_quiescence(
    service, app_runtime, runner, scripted_model, runs_kit
):
    await runs_kit.pause_sweeps(service)
    runtime, ws = app_runtime, service.tenant.workspace_id
    agent = await runs_kit.create_agent(service, scripted_model)
    body = threads.NewThread.model_validate(runs_kit.message(agent, "first", delivery="next_run"))

    async def create():
        async with transaction(runtime.storage) as session:
            admission = await threads.prepare_admission(session, runtime, ws, runner, app_id="claw")
            return await threads.create_thread_in_transaction(
                session, runtime, admission, ws, body, request_key="claw:thread"
            )

    with pytest.raises(ValueError, match="rollback"):
        async with transaction(runtime.storage) as session:
            admission = await threads.prepare_admission(session, runtime, ws, runner, app_id="claw")
            rolled, receipt = await threads.create_thread_in_transaction(
                session, runtime, admission, ws, body, request_key="claw:thread"
            )
            assert receipt.run is not None
            raise ValueError("rollback")
    async with short_session(runtime.storage) as session:
        assert await session.get(ThreadRow, rolled.id) is None
    thread, receipt = await create()
    assert receipt.run is not None and thread.current_run_id == receipt.run.id
    replay, replay_receipt = await create()
    assert replay.id == thread.id and replay_receipt.entry.id == receipt.entry.id
    async with transaction(runtime.storage) as session:
        admission = await threads.prepare_admission(session, runtime, ws, runner, app_id="claw")
        submitted, created = await threads.submit_message_in_transaction(
            session,
            runtime,
            admission,
            ws,
            thread.id,
            threads.Message.model_validate(runs_kit.message(agent, "next", delivery="next_run")),
            request_key="claw:message",
        )
        assert created and submitted.entry.id != receipt.entry.id
        assert (await threads.get_thread_activity(session, ws, thread.id)).has_pending_native_entries
        assert not (await threads.list_child_threads(session, ws, session_id=thread.session_id)).items
        await threads.update_thread_resources(
            session, runtime, ws, app_id="claw", principal_id=runner, root_thread_id=thread.id, browser_id=None
        )
    with pytest.raises(ServiceError) as busy:
        async with transaction(runtime.storage) as session:
            await threads.update_thread_resources(
                session, runtime, ws, app_id="claw", principal_id=runner, root_thread_id=thread.id, environment_id=None
            )
    assert busy.value.details["reason"] == "thread_resources_busy"


async def test_native_project_revision_and_schedule_facades(service, app_runtime, runner, scripted_model, runs_kit):
    runtime, tenant = app_runtime, service.tenant
    ws, actor = tenant.workspace_id, tenant.principal_id
    agent = await runs_kit.create_agent(service, scripted_model)
    async with transaction(runtime.storage) as session:
        space = await projects.create_memory_space(session, runtime, ws, app_id="claw", actor_id=actor, name="Project")
        assert space.startswith("mspace_")
        await projects.check_resources(session, runtime, ws, principal_id=runner)
        agent_id = agent["id"]
        head, default = await agents.get_agent_default_revision(session, ws, agent_id)
        unchanged = await agents.publish_derived_revision(
            session,
            runtime,
            ws,
            app_id="claw",
            actor_id=actor,
            agent_id=agent_id,
            mutate_config=lambda config: config,
            note=None,
        )
        assert unchanged.id == default.id == head.default_revision_id
        derived = await agents.publish_derived_revision(
            session,
            runtime,
            ws,
            app_id="claw",
            actor_id=actor,
            agent_id=agent_id,
            mutate_config=lambda config: config.model_copy(update={"instructions": "Derived instructions"}),
            note=None,
        )
        assert derived.id != default.id
        assert (await agents.get_agent_default_revision(session, ws, agent_id))[1].id == default.id
        session_id = await schedules.ensure_schedule_session(
            session,
            runtime,
            ws,
            app_id="claw",
            principal_id=runner,
            existing_session_id=None,
            session_create=SessionCreate(title="Scheduled"),
        )
        body = ScheduleCreate.model_validate(
            {
                "name": "Nightly",
                "schedule": {"type": "once", "run_at": datetime.now(UTC) + timedelta(hours=2)},
                "invocation": {
                    "agent_id": agent_id,
                    "agent_revision_id": None,
                    "payload": runs_kit.message(agent, "run")["payload"],
                },
                "target": {"session_id": session_id},
            }
        )
        result, created = await schedules.create_fresh_schedule(
            session, runtime, ws, app_id="claw", actor_id=actor, body=body, request_key="claw:schedule"
        )
        assert created and result.labels == {"app": "claw"}
        assert (await schedules.list_app_schedules(session, ws, app_id="claw")).items == [result]
        assert await schedules.count_active_fresh_schedules(session, ws, app_id="other") == 0
        assert await schedules.count_active_fresh_schedules(session, ws, app_id="claw") == 1


def test_usage_exports_native_contract():
    from a13n_service.usage.schemas import AppModelUsageQuery
    from a13n_service.usage.service import app_models

    assert usage.AppModelUsageQuery is AppModelUsageQuery
    assert usage.app_models is app_models


async def test_rebind_shared_primary_retains_desired_policy_and_freezes_it_on_next_run(
    service,
    app_runtime,
    runner,
    scripted_model,
    runs_kit,
    monkeypatch,
):
    from a13n_service.app_sdk import _thread_resources
    from a13n_service.runs.environments.tables import ThreadEnvironmentRow, ThreadSessionBindingRow
    from a13n_service.runs.schemas import Outcome
    from a13n_service.runs.seal import seal
    from a13n_service.runs.tables import RunRow, SessionRow
    from a13n_service.runs.threads import new_thread

    from .environments_support import with_fake_providers
    from .test_session_credentials import selection

    env = await with_fake_providers(service)
    runtime = replace(env.runtime, apps=app_runtime.apps)
    ws = service.tenant.workspace_id
    agent = await runs_kit.delegating(service, scripted_model, "async")
    inputs = dict(app_id="claw", principal_id=runner)
    async with transaction(runtime.storage) as db:
        env_a, env_b = [
            await projects.reserve_environment(
                db,
                runtime,
                ws,
                **inputs,
                template_id=env.template["id"],
                name=name,
            )
            for name in ("A", "B")
        ]
    read = await egress.create_app_credential(
        runtime,
        ws,
        app_id="claw",
        actor_id=runner,
        name="App egress",
        allowed_hosts=["api.github.com"],
        value="synthetic",
    )
    configuration = threads.SessionConfiguration.model_validate(selection(read.id))
    async with transaction(runtime.storage) as db:
        admission = await threads.prepare_admission(db, runtime, ws, runner, app_id="claw")
        body = threads.NewThread.model_validate(
            runs_kit.message(
                agent,
                "first",
                environments=[{"name": "workspace", "environment_id": env_a}],
            )
        )
        root, receipt = await threads.create_thread_in_transaction(
            db, runtime, admission, ws, body, request_key="claw:new"
        )
        assert receipt.run is not None
    async with transaction(runtime.storage) as db:
        parent = await db.get_one(ThreadRow, root.id)
        origin = await db.get_one(RunRow, receipt.run.id)
        await seal(db, runtime, parent, origin, None, Outcome.cancelled(), at=datetime.now(UTC))
        owner = await db.get_one(SessionRow, root.session_id)
        children = [
            new_thread(
                owner,
                mcp_headers={},
                origin="child",
                origin_thread_id=root.id,
                origin_run_id=origin.id,
                origin_tool_call_id=key,
                subagent="helper",
            )
            for key in ("one", "two")
        ]
        db.add_all(children)
        await db.flush()
        child_ids = sorted(child.id for child in children)
        grandchild = new_thread(
            owner,
            mcp_headers={},
            origin="child",
            origin_thread_id=child_ids[0],
            origin_run_id=origin.id,
            origin_tool_call_id="three",
            subagent="helper",
        )
        db.add(grandchild)
        await db.flush()
        grandchild_id = grandchild.id
    locks = []
    get_thread = _thread_resources.get_thread

    async def track(db, scope, thread_id, **kwargs):
        locks.append(thread_id)
        return await get_thread(db, scope, thread_id, **kwargs)

    monkeypatch.setattr(_thread_resources, "get_thread", track)
    async with transaction(runtime.storage) as db:
        listed = await threads.list_child_threads(db, ws, session_id=root.session_id, origin_thread_id=root.id, limit=1)
        rest = await threads.list_child_threads(
            db, ws, session_id=root.session_id, origin_thread_id=root.id, limit=1, cursor=listed.next_cursor
        )
        assert listed.next_cursor and sorted(item.id for item in (*listed.items, *rest.items)) == child_ids
        await threads.update_thread_resources(
            db, runtime, ws, **inputs, root_thread_id=root.id, environment_id=env_b, session_configuration=configuration
        )
        assert locks == [root.id, *child_ids, grandchild_id]
        for thread_id in locks:
            mount = await db.get_one(ThreadEnvironmentRow, (thread_id, "workspace"))
            assert mount.environment_id == env_b
        await threads.update_thread_resources(db, runtime, ws, **inputs, root_thread_id=root.id, environment_id=env_a)
        saved = await db.get_one(ThreadSessionBindingRow, (root.id, env_a, "default"))
        assert threads.SessionConfiguration.model_validate(saved.configuration) == configuration
        admission = await threads.prepare_admission(db, runtime, ws, runner, app_id="claw")
        message = threads.Message.model_validate(runs_kit.message(agent, "second", delivery="next_run"))
        next_run, created = await threads.submit_message_in_transaction(
            db, runtime, admission, ws, root.id, message, request_key="claw:next"
        )
        assert created and next_run.run is not None
        [mount] = next_run.run.environment_mounts
        assert mount.environment_id == env_a and mount.session_configuration == configuration
        assert mount.session_version == 2


async def test_dynamic_child_blocks_rebinding_until_quiescent_then_inherits_new_policy(
    service,
    app_runtime,
    runner,
    scripted_model,
    runs_kit,
):
    from a13n_service.runs.attempts import AttemptControl
    from a13n_service.runs.claim import claim
    from a13n_service.runs.dynamic_subagents import Spawn
    from a13n_service.runs.environments.tables import ThreadEnvironmentRow, ThreadSessionBindingRow
    from a13n_service.runs.schemas import Outcome
    from a13n_service.runs.seal import seal_attempt
    from a13n_service.runs.subagents import ChildRuns
    from a13n_service.runs.tables import RunRow

    from .environments_support import with_fake_providers
    from .test_session_credentials import selection

    env = await with_fake_providers(service)
    runtime = replace(env.runtime, apps=app_runtime.apps)
    ws = service.tenant.workspace_id
    agent = await runs_kit.create_agent(service, scripted_model, subagents={"dynamic": {"enabled": True}})
    async with transaction(runtime.storage) as db:
        old, new = [
            await projects.reserve_environment(
                db,
                runtime,
                ws,
                app_id="claw",
                principal_id=runner,
                template_id=env.template["id"],
                name=name,
            )
            for name in ("Old", "New")
        ]
    credential = await egress.create_app_credential(
        runtime,
        ws,
        app_id="claw",
        actor_id=runner,
        name="App egress",
        allowed_hosts=["api.github.com"],
        value="synthetic",
    )
    desired = threads.SessionConfiguration.model_validate(selection(credential.id))
    parent = await runs_kit.start_thread(
        service, agent, "Parent", environments=[{"name": "workspace", "environment_id": old}]
    )
    (parent_lease,) = await claim(runtime, worker_id="parent", worker_build="test", limit=1)
    spawned = await ChildRuns(runtime, parent_lease, AttemptControl(), {}).spawn(
        Spawn(name="worker", instructions="Task role", prompt="Child", selection={"toolsets": []}),
        "dynamic-child",
    )
    (child_lease,) = await claim(runtime, worker_id="child", worker_build="test", limit=1)
    assert child_lease.run_id == spawned.execution_id
    await seal_attempt(runtime, parent_lease, Outcome.cancelled())
    root_id, child_id = parent["thread"]["id"], child_lease.thread_id

    async def rebind():
        async with transaction(runtime.storage) as db:
            return await threads.update_thread_resources(
                db,
                runtime,
                ws,
                app_id="claw",
                principal_id=runner,
                root_thread_id=root_id,
                environment_id=new,
                session_configuration=desired,
            )

    with pytest.raises(ServiceError) as busy:
        await rebind()
    assert busy.value.details["reason"] == "thread_resources_busy"
    assert busy.value.details["id"] == child_id
    async with short_session(runtime.storage) as db:
        assert (await db.get_one(ThreadEnvironmentRow, (child_id, "workspace"))).environment_id == old
    await seal_attempt(runtime, child_lease, Outcome.cancelled())
    assert (await rebind()).environment_id == new
    async with short_session(runtime.storage) as db:
        for thread_id in (root_id, child_id):
            assert (await db.get_one(ThreadEnvironmentRow, (thread_id, "workspace"))).environment_id == new
            binding = await db.get_one(ThreadSessionBindingRow, (thread_id, new, "default"))
            assert threads.SessionConfiguration.model_validate(binding.configuration) == desired
        historical = await db.get_one(RunRow, child_lease.run_id)
        assert historical.environment_mounts[0]["environment_id"] == old


async def test_template_rebinding_queues_and_audits_creation_and_returns_root_ids(service, app_runtime, runner):
    from a13n_service.runs.browsers.tables import ThreadBrowserRow
    from a13n_service.runs.environments.tables import EnvironmentRow, ThreadEnvironmentRow
    from a13n_service.runs.sessions import new_session
    from a13n_service.runs.threads import new_thread

    from .environments_support import with_fake_providers

    env = await with_fake_providers(service)
    runtime = replace(env.runtime, apps=app_runtime.apps)
    tenant = service.tenant
    ws = tenant.workspace_id
    # Register only the desired browser resource; no provider connection is needed for rebinding.
    registered = await service.client.post(
        f"{service.api}/browsers/attach",
        json={
            "name": "Project browser",
            "cdp_url": "wss://browser.example/devtools/browser/project",
        },
    )
    assert registered.status_code == 201, registered.text
    browser_id = registered.json()["id"]
    async with transaction(runtime.storage) as db:
        owner = new_session(tenant.organization_id, ws, runner)
        root = new_thread(owner, mcp_headers={})
        db.add(owner)
        await db.flush()
        db.add(root)
        await db.flush()
        updated = await threads.update_thread_resources(
            db,
            runtime,
            ws,
            app_id="claw",
            principal_id=runner,
            root_thread_id=root.id,
            environment_template_id=env.template["id"],
            template_name="Claw replacement",
            browser_id=browser_id,
        )
        assert isinstance(updated, threads.UpdatedThreadResources)
        assert updated.environment_id is not None and updated.browser_id == browser_id
        root_id = root.id
    async with short_session(runtime.storage) as db:
        row = await db.get_one(EnvironmentRow, updated.environment_id)
        assert (row.status, row.name) == ("creating", "Claw replacement") and row.operation_id is not None
        assert (await db.get_one(ThreadEnvironmentRow, (root_id, "workspace"))).environment_id == row.id
        assert (await db.get_one(ThreadBrowserRow, root_id)).browser_id == browser_id
        event = (
            await db.scalars(
                select(AuditEventRow).where(
                    AuditEventRow.target_id == row.id,
                    AuditEventRow.action == "environment.create",
                )
            )
        ).one()
        assert event.actor_id == runner and event.workspace_id == ws
    async with transaction(runtime.storage) as db:
        args = dict(app_id="claw", principal_id=runner, root_thread_id=root_id)
        assert await threads.update_thread_resources(db, runtime, ws, **args) == updated
        assert await threads.update_thread_resources(db, runtime, ws, **args, browser_id=None) == (
            threads.UpdatedThreadResources(updated.environment_id, None)
        )


async def test_fresh_schedules_filter_instance_labels_and_count_archived_pending(
    service, app_runtime, scripted_model, runs_kit
):
    from a13n_service.infra.db import now
    from a13n_service.runs.schedules.lifecycle import occurrence
    from a13n_service.runs.schedules.tables import ScheduleRow

    await runs_kit.pause_sweeps(service)
    runtime, tenant = app_runtime, service.tenant
    ws = tenant.workspace_id
    agent = await runs_kit.create_agent(service, scripted_model)
    async with transaction(runtime.storage) as db:

        async def make(key, instance, app_id="claw"):
            body = ScheduleCreate.model_validate(
                {
                    "name": key,
                    "labels": {"instance": instance, "kind": "workflow"},
                    "schedule": {"type": "once", "run_at": datetime.now(UTC) + timedelta(hours=2)},
                    "invocation": {
                        "agent_id": agent["id"],
                        "agent_revision_id": None,
                        "payload": runs_kit.message(agent, "run")["payload"],
                    },
                }
            )
            result, created = await schedules.create_fresh_schedule(
                db,
                runtime,
                ws,
                app_id=app_id,
                actor_id=tenant.principal_id,
                body=body,
                request_key=key,
            )
            assert created
            return result.id

        ids_a = {await make("a1", "a"), await make("a2", "a")}
        b = await make("b", "b")
        await make("other", "a", app_id="other")
        current = await now(db)
        row = await db.get_one(ScheduleRow, b)
        pending = await occurrence(
            db,
            runtime,
            row,
            current,
            trigger="manual",
            requested_by_id=tenant.principal_id,
            request_key="pending-b",
            request_digest="digest",
            status="pending",
        )
        # Exercise the stored-state predicate; the normal archive command first cancels unsubmitted work.
        row.state, row.pause_reason, row.archived_at = "archived", None, current
        await db.flush()
        assert row.pending_occurrence_id == pending.id
        assert await schedules.count_active_fresh_schedules(db, ws, app_id="claw", labels={"instance": "b"}) == 1
        assert await schedules.count_active_fresh_schedules(db, ws, app_id="claw", labels={"instance": "a"}) == 2
        page = await schedules.list_app_schedules(
            db, ws, app_id="claw", labels={"instance": "a", "kind": "workflow"}, limit=1
        )
        assert page.next_cursor is not None
        follow = await schedules.list_app_schedules(
            db, ws, app_id="claw", labels={"kind": "workflow", "instance": "a"}, cursor=page.next_cursor
        )
        assert {item.id for item in (*page.items, *follow.items)} == ids_a
        with pytest.raises(ServiceError) as wrong_filter:
            await schedules.list_app_schedules(db, ws, app_id="claw", labels={"instance": "b"}, cursor=page.next_cursor)
        assert wrong_filter.value.code == "invalid_cursor"
        assert [
            item.id
            for item in (
                await schedules.list_app_schedules(
                    db,
                    ws,
                    app_id="claw",
                    labels={"instance": "b", "app": "other"},
                )
            ).items
        ] == [b]
        idle = await db.get_one(ScheduleRow, await make("idle", "idle"))
        idle.state, idle.pause_reason, idle.archived_at = "archived", None, current
        await db.flush()
        assert await schedules.count_active_fresh_schedules(db, ws, app_id="claw", labels={"instance": "idle"}) == 0
