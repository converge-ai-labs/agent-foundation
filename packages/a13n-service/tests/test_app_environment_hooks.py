"""Managed App hook phases and durable recovery against PostgreSQL."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_service.app_plugins import AppCatalog, AppManifest
from a13n_service.app_sdk import EnvironmentHookAbort, projects
from a13n_service.app_sdk.hooks import EnvironmentLifecycleHook
from a13n_service.infra.db import now, transaction
from a13n_service.runs.environments import lifecycle
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.tenancy.tables import OrganizationRow

from .environments_support import BACKEND, act, stored, with_fake_providers

pytestmark = pytest.mark.anyio


async def setup(service, hooks):
    env = await with_fake_providers(service)
    response = await env.client.post(f"{env.workspace}/service-accounts", json={"name": "hooks-runner"})
    assert response.status_code == 201, response.text
    env.runtime = replace(
        env.runtime,
        apps=AppCatalog(tuple(AppManifest(app, app, app, "1") for app in ("seed", "ops", "muted"))),
        environment_hooks=tuple(hooks),
    )
    env.app.state.runtime = env.runtime
    async with transaction(env.runtime.storage) as db:
        org = await db.get_one(OrganizationRow, env.tenant.organization_id)
        org.settings = {**org.settings, "app_plugins": {"enabled": ["seed", "ops"]}}
        environment_id = await projects.reserve_environment(
            db,
            env.runtime,
            env.tenant.workspace_id,
            app_id="seed",
            app_context={"instance": "a"},
            principal_id=response.json()["id"],
            template_id=env.template["id"],
        )
    return env, environment_id


async def test_lifecycle_hook_retry_preserves_receipt_and_completed_apps(service):
    successful, attempts = [], []

    async def seed(context):
        assert context.owner_app_id == context.app_id == "seed"
        assert context.app_context == {"instance": "a"} and context.working_directory == "/work"
        assert (await stored(service, context.environment_id)).status == "creating"
        assert BACKEND.instances[context.environment_id] == "running"
        successful.append(context.operation_id)

    async def restore(context):
        attempts.append(context.operation_id)
        if len(attempts) == 1:
            raise EnvironmentHookAbort("restore_pending", "Restore is not yet available", permanent=False)

    async def unexpected(context):
        pytest.fail(f"Unmatched hook ran: {context.app_id}")

    env, environment_id = await setup(
        service,
        [
            ("seed", EnvironmentLifecycleHook("after_create", seed, timeout=6)),
            ("ops", EnvironmentLifecycleHook("after_create", restore, timeout=8, scope="organization")),
            ("ops", EnvironmentLifecycleHook("after_create", unexpected)),
            ("muted", EnvironmentLifecycleHook("after_create", unexpected, scope="organization")),
        ],
    )
    operation = await lifecycle.claim(env.runtime, environment_id, owner="test")
    assert operation is not None and operation.hooks is not None
    async with transaction(env.runtime.storage) as db:
        row = await db.get_one(EnvironmentRow, environment_id)
        assert (row.operation_deadline - await now(db)).total_seconds() > operation.seconds + 14
    await lifecycle.dispatch(env.runtime, operation)
    blocked = await stored(env, environment_id)
    assert blocked.status == "creating" and blocked.failure["code"] == "restore_pending"
    assert blocked.operation_progress["provider_completed"] and blocked.handle["state"] is not None
    assert blocked.operation_progress["hooks_completed"] == ["seed"]
    await lifecycle.advance(env.runtime, environment_id, owner="retry")
    ready = await stored(env, environment_id)
    assert ready.status == "ready" and ready.operation_progress is None
    assert attempts == [operation.operation_id, operation.operation_id]
    assert successful == BACKEND.preparations == [operation.operation_id]


async def test_warmup_publishes_after_create_hook_after_text_run_seals(service, scripted_model, runs_kit):
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def seed(context):
        calls.append(context.operation_id)
        entered.set()
        await release.wait()

    env, environment_id = await setup(service, [("seed", EnvironmentLifecycleHook("after_create", seed, timeout=15))])
    await runs_kit.pause_sweeps(env)
    agent = await runs_kit.create_agent(env, scripted_model, lazy_environment="warmup")
    inference = asyncio.Event()
    scripted_model.say("Text only", gate=inference)
    submitted = await runs_kit.start_thread(
        env, agent, "hello", environments=[{"name": "workspace", "environment_id": environment_id}]
    )
    run_id = submitted["run"]["id"]
    task = await runs_kit.attempt(env)
    try:
        await asyncio.wait_for(entered.wait(), 5)
        blocked = await stored(env, environment_id)
        assert blocked.status == "creating" and blocked.operation_progress["provider_completed"]
        assert calls == BACKEND.preparations == [blocked.operation_id]
        inference.set()
        await asyncio.wait_for(asyncio.shield(task), 5)
        assert (await runs_kit.sealed(env, run_id))["status"] == "completed"
        assert (await stored(env, environment_id)).operation_id == blocked.operation_id
    finally:
        inference.set()
        release.set()
        await task

    async with asyncio.timeout(5):
        while (ready := await stored(env, environment_id)).status != "ready":
            await asyncio.sleep(0.05)
    assert ready.operation_id is None
    assert calls == BACKEND.preparations == [blocked.operation_id]


async def test_lifecycle_interruption_keeps_native_receipt(service):
    entered = asyncio.Event()
    attempts = []

    async def seed(context):
        attempts.append(context.operation_id)
        if len(attempts) == 1:
            entered.set()
            await asyncio.Future()

    env, environment_id = await setup(service, [("seed", EnvironmentLifecycleHook("after_create", seed))])
    operation = await lifecycle.claim(env.runtime, environment_id, owner="test")
    assert operation is not None
    task = asyncio.create_task(lifecycle.dispatch(env.runtime, operation))
    async with asyncio.timeout(3):
        await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    current = await stored(env, environment_id)
    assert current.operation_progress["provider_completed"] and current.handle["state"] is not None
    await lifecycle.advance(env.runtime, environment_id, owner="retry")
    assert (await stored(env, environment_id)).status == "ready"
    assert attempts == [operation.operation_id] * 2
    assert BACKEND.preparations == [operation.operation_id]


@pytest.mark.parametrize("delete_ready", [True, False])
async def test_live_hooks_do_not_prevent_destruction_and_skip_stopped(service, delete_ready):
    seen = []

    async def timeout(context):
        await asyncio.sleep(10)

    async def stop(context):
        assert BACKEND.instances[context.environment_id] == "running"
        seen.append(context.phase)
        if seen.count("before_stop") == 1:
            raise EnvironmentHookAbort("snapshot_pending", "Snapshot is still running", permanent=False)

    async def delete(context):
        assert BACKEND.instances[context.environment_id] == "running"
        seen.append(context.phase)
        raise EnvironmentHookAbort("archive_rejected", "Archive failed")

    async def start(context):
        assert BACKEND.instances[context.environment_id] == "running"
        seen.append(context.phase)
        raise RuntimeError("Internal configuration detail")

    env, environment_id = await setup(
        service,
        [
            ("seed", EnvironmentLifecycleHook("after_create", timeout, timeout=0.02)),
            ("seed", EnvironmentLifecycleHook("before_stop", stop)),
            ("seed", EnvironmentLifecycleHook("before_delete", delete)),
            ("seed", EnvironmentLifecycleHook("after_start", start)),
        ],
    )
    await lifecycle.advance(env.runtime, environment_id, owner="test")
    assert (await stored(env, environment_id)).status == "ready"
    assert (await act(env, "POST", f"{environment_id}/stop"))[0] == 202
    await lifecycle.advance(env.runtime, environment_id, owner="test")
    blocked = await stored(env, environment_id)
    assert blocked.status == "stopping" and blocked.failure["code"] == "snapshot_pending"
    assert blocked.operation_progress["hook_active"] and BACKEND.instances[environment_id] == "running"
    await lifecycle.advance(env.runtime, environment_id, owner="retry")
    assert (await stored(env, environment_id)).status == "stopped"
    if delete_ready:
        async with transaction(env.runtime.storage) as db:
            row = await db.get_one(EnvironmentRow, environment_id)
            await lifecycle.begin(db, row, "starting")
        await lifecycle.advance(env.runtime, environment_id, owner="test")
        assert (await stored(env, environment_id)).status == "ready"
    assert (await act(env, "DELETE", environment_id))[0] == 202
    await lifecycle.advance(env.runtime, environment_id, owner="test")
    assert (await stored(env, environment_id)).status == "deleted" and environment_id not in BACKEND.instances
    assert seen == ["before_stop", "before_stop"] + (["after_start", "before_delete"] if delete_ready else [])


async def test_permanent_hook_rejects_ready_without_losing_destroy_handle(service):
    async def reject(context):
        raise EnvironmentHookAbort("unsupported_image", "This image does not support initialization")

    env, environment_id = await setup(service, [("seed", EnvironmentLifecycleHook("after_create", reject))])
    await lifecycle.advance(env.runtime, environment_id, owner="test")
    row = await stored(env, environment_id)
    assert (
        row.failure["permanent"]
        and row.operation_progress["provider_completed"]
        and row.operation_progress["hook_blocked"]
    )
    assert await lifecycle.claim(env.runtime, environment_id, owner="retry") is None
    assert (await act(env, "DELETE", environment_id))[0] == 202
    await lifecycle.advance(env.runtime, environment_id, owner="test")
    assert (await stored(env, environment_id)).status == "deleted" and environment_id not in BACKEND.instances


async def test_expired_pre_hook_claim_resumes_before_provider_stop(service):
    seen = []

    async def stop(context):
        seen.append(context.operation_id)
        assert BACKEND.instances[context.environment_id] == "running"

    env, environment_id = await setup(service, [("seed", EnvironmentLifecycleHook("before_stop", stop))])
    await lifecycle.advance(env.runtime, environment_id, owner="test")
    assert (await act(env, "POST", f"{environment_id}/stop"))[0] == 202
    operation = await lifecycle.claim(env.runtime, environment_id, owner="lost")
    assert operation is not None and operation.hooks is not None
    async with transaction(env.runtime.storage) as db:
        row = await db.get_one(EnvironmentRow, environment_id)
        row.lease_expires_at = (await now(db)) - timedelta(seconds=1)
    await lifecycle.advance(env.runtime, environment_id, owner="retry")
    await lifecycle.dispatch(env.runtime, replace(operation, dispatch_before=0))
    assert (await stored(env, environment_id)).status == "stopped"
    assert seen == [operation.operation_id]


async def test_environment_events_deliver_committed_transitions_by_scope(service):
    from a13n_service.app_definitions import AppDefinition, EnvironmentEventSubscription, InstalledApp
    from a13n_service.app_plugin_runtime import app_environment_events
    from a13n_service.runs.environments import events

    env, environment_id = await setup(service, [])
    env.runtime = replace(env.runtime, has_environment_subscribers=True)
    env.app.state.runtime = env.runtime
    queue = asyncio.Queue()

    def subscriber(app_id, scope="app"):
        async def receive(runtime, event):
            await queue.put((app_id, event))

        return InstalledApp(
            AppDefinition(app_id, app_id, environment_events=(EnvironmentEventSubscription(receive, scope=scope),)),
            "test",
            "1",
        )

    # The shared group exists before publishing; consumption itself happens in a separate task.
    await env.runtime.redis.delete(events.STREAM_KEY)
    await env.runtime.redis.xgroup_create(events.STREAM_KEY, events.GROUP, id="$", mkstream=True)
    consumer = asyncio.create_task(
        app_environment_events(
            env.runtime,
            (
                subscriber("seed"),
                subscriber("ops"),
                subscriber("ops", "organization"),
                subscriber("muted", "organization"),
            ),
        )
    )
    try:
        with pytest.raises(ValueError, match="rollback"):
            async with transaction(env.runtime.storage) as db:
                row = await db.get_one(EnvironmentRow, environment_id)
                events.publish_after_commit(db, env.runtime, row, "deleted", reason="explicit")
                raise ValueError("rollback")
        assert await env.runtime.redis.xlen(events.STREAM_KEY) == 0

        async def check_transition(kind, *, origin=None, reason=None):
            results = [await asyncio.wait_for(queue.get(), 3) for _ in range(2)]
            assert {app_id for app_id, _ in results} == {"seed", "ops"}
            for _, event in results:
                assert (event.event, event.origin, event.reason) == (kind, origin, reason)
                assert event.environment_id == environment_id and event.kind == "managed"
                assert event.app_id == "seed" and event.app_context == {"instance": "a"}
                assert event.workspace_id == env.tenant.workspace_id

        await lifecycle.advance(env.runtime, environment_id, owner="test")
        await check_transition("ready", origin="create")
        assert (await act(env, "POST", f"{environment_id}/stop"))[0] == 202
        await lifecycle.advance(env.runtime, environment_id, owner="test")
        await check_transition("stopped")
        async with transaction(env.runtime.storage) as db:
            await lifecycle.begin(db, await db.get_one(EnvironmentRow, environment_id), "starting")
        await lifecycle.advance(env.runtime, environment_id, owner="test")
        await check_transition("ready", origin="start")
        assert (await act(env, "DELETE", environment_id))[0] == 202
        await lifecycle.advance(env.runtime, environment_id, owner="test")
        await check_transition("deleted", reason="explicit")
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(queue.get(), 0.15)
    finally:
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer


async def test_owned_environment_loss_and_replacement_events_keep_provenance(service, runs_kit):
    from a13n_service.app_sdk import threads
    from a13n_service.runs.environments import events

    from .environments_support import interrupt
    from .test_environments_renewal import expiring_template, lose_sandbox

    env, _ = await setup(service, [])
    env.runtime = replace(env.runtime, has_environment_subscribers=True)
    env.app.state.runtime = env.runtime
    context = {"instance": "recoverable"}
    workspace = env.tenant.workspace_id
    account = await env.client.post(f"{env.workspace}/service-accounts", json={"name": "recovery-runner"})
    assert account.status_code == 201, account.text
    actor = account.json()["id"]
    template = await expiring_template(env)
    await env.runtime.redis.delete(events.STREAM_KEY)
    async with transaction(env.runtime.storage) as db:
        original = await projects.reserve_environment(
            db,
            env.runtime,
            workspace,
            app_id="seed",
            app_context=context,
            principal_id=actor,
            template_id=template,
        )
    await lifecycle.advance(env.runtime, original, owner="test")
    body = threads.NewThread.model_validate(
        runs_kit.message(env.agent, "first", environments=[{"name": "workspace", "environment_id": original}])
    )
    async with transaction(env.runtime.storage) as db:
        admission = await threads.prepare_admission(
            db, env.runtime, workspace, actor, app_id="seed", app_context=context
        )
        root, result = await threads.create_thread_in_transaction(
            db, env.runtime, admission, workspace, body, request_key="prepare-recovery"
        )
    assert result.run is not None
    await interrupt(env, result.run.id)
    await lose_sandbox(env, original)
    async with transaction(env.runtime.storage) as db:
        admission = await threads.prepare_admission(
            db, env.runtime, workspace, actor, app_id="seed", app_context=context
        )
        continued, created = await threads.submit_message_in_transaction(
            db,
            env.runtime,
            admission,
            workspace,
            root.id,
            threads.Message.model_validate(runs_kit.message(env.agent, "continue")),
            request_key="recover-lost",
        )
    assert created and continued.run is not None
    successor = await stored(env, continued.run.environment_mounts[0].environment_id)
    assert (successor.app_id, successor.app_context, successor.replaces_environment_id) == ("seed", context, original)
    async with asyncio.timeout(3):
        while len(records := await env.runtime.redis.xrange(events.STREAM_KEY)) < 3:
            await asyncio.sleep(0.01)
    payloads = {e.event: e for _, raw in records if (e := events.EnvironmentEvent.model_validate_json(raw["data"]))}
    assert payloads["lost"].reason == "environment_lost" and payloads["lost"].environment_id == original
    assert (
        payloads["recreated"].environment_id == successor.id
        and payloads["recreated"].replaces_environment_id == original
    )
    assert all(e.app_id == "seed" and e.app_context == context for e in payloads.values())
