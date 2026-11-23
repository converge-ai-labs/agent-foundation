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
