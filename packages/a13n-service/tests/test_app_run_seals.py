"""Run seal hint handoff is post-commit, lossy and bounded."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from a13n_service.app_definitions import AppDefinition, InstalledApp, RunSealSubscription
from a13n_service.app_plugins import AppCatalog
from a13n_service.infra.db import transaction
from a13n_service.runs.seal_events import RunSealEvent, channel, publish_after_commit

pytestmark = pytest.mark.anyio


async def test_seal_hints_commit_rollback_and_no_subscribers(service):
    async def callback(runtime, event):
        pass

    app = InstalledApp(AppDefinition("test.seals", "Seals", run_seals=(RunSealSubscription(callback),)), "test", "1")
    runtime = replace(service.runtime, apps=AppCatalog((app.manifest,)))
    run = SimpleNamespace(
        organization_id="org_test",
        workspace_id="ws_test",
        session_id="ses_test",
        thread_id="thr_test",
        id="run_test",
        principal_id="prn_test",
        labels={},
    )
    async with runtime.redis.pubsub() as listener:
        await listener.subscribe(channel(runtime))
        await listener.get_message(timeout=1)
        with pytest.raises(ValueError):
            async with transaction(runtime.storage) as session:
                await publish_after_commit(session, runtime, run, "completed")
                raise ValueError("rollback")
        assert await listener.get_message(ignore_subscribe_messages=True, timeout=0.1) is None
        async with transaction(runtime.storage) as session:
            await publish_after_commit(session, replace(runtime, apps=AppCatalog()), run, "completed")
        assert await listener.get_message(ignore_subscribe_messages=True, timeout=0.1) is None
        async with transaction(runtime.storage) as session:
            await publish_after_commit(session, runtime, run, "completed")
        async with asyncio.timeout(2):
            while True:
                message = await listener.get_message(ignore_subscribe_messages=True, timeout=0.1)
                if message:
                    break
        event = RunSealEvent.model_validate_json(message["data"])
        assert event.run_id == run.id
        assert event.status == "completed"
        assert event.labels == run.labels


async def test_seal_publish_handoff_is_nonblocking_and_bounded(service, monkeypatch):
    from a13n_service.runs import seal_events

    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked(runtime, event):
        started.set()
        await release.wait()

    monkeypatch.setattr(seal_events, "_publish", blocked)
    event = RunSealEvent(
        organization_id="o",
        workspace_id="w",
        session_id="s",
        thread_id="t",
        run_id="r",
        principal_id="p",
        status="failed",
        labels={},
    )
    runtime = replace(service.runtime)
    for _ in range(40):
        await seal_events._handoff(runtime, event)
    assert len(runtime.seal_publish_tasks) == 32
    await asyncio.wait_for(started.wait(), 1)
    release.set()
    await asyncio.gather(*runtime.seal_publish_tasks)


async def test_subscription_admits_current_enabled_organization_and_status(service):
    from a13n_service.app_plugin_runtime import app_run_seals
    from a13n_service.tenancy.tables import OrganizationRow

    calls = asyncio.Queue()

    async def callback(runtime, event):
        await calls.put(event.status)

    app = InstalledApp(AppDefinition("test.seals", "Seals", run_seals=(RunSealSubscription(callback),)), "test", "1")
    organization_id = "org_seal_test"
    async with transaction(service.runtime.storage) as session:
        session.add(OrganizationRow(id=organization_id, name="Seals", settings={}))
    runtime = replace(service.runtime, apps=AppCatalog((app.manifest,)))
    task = asyncio.create_task(app_run_seals(runtime, (app,)))
    event = RunSealEvent(
        organization_id=organization_id,
        workspace_id="w",
        session_id="s",
        thread_id="t",
        run_id="r",
        principal_id="p",
        status="completed",
        labels={},
    )
    try:
        async with asyncio.timeout(3):
            while (await runtime.redis.pubsub_numsub(channel(runtime)))[0][1] == 0:
                await asyncio.sleep(0.01)
        await runtime.redis.publish(channel(runtime), event.model_dump_json())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(calls.get(), 0.15)
        async with transaction(runtime.storage) as session:
            organization = await session.get_one(OrganizationRow, organization_id)
            organization.settings = {"app_plugins": {"enabled": ["test.seals"]}}
        await runtime.redis.publish(channel(runtime), event.model_copy(update={"status": "waiting"}).model_dump_json())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(calls.get(), 0.15)
        await runtime.redis.publish(channel(runtime), event.model_dump_json())
        assert await asyncio.wait_for(calls.get(), 2) == "completed"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_native_inbox_transaction_rollback_and_replay(service, scripted_model, runs_kit):
    from a13n_service.infra.errors import ServiceError
    from a13n_service.runs.schemas import Message
    from a13n_service.runs.submit import submit_message, submit_message_in_transaction
    from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal

    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    thread_id = (await runs_kit.start_thread(service, agent, "first"))["thread"]["id"]
    tenant = service.tenant
    actor = Principal(tenant.principal_id, "user", (Grant(tenant.organization_id, None, BUILT_IN_ROLES["admin"]),))
    message = Message.model_validate(runs_kit.message(agent, "next", delivery="next_run"))
    with pytest.raises(ValueError):
        async with transaction(service.runtime.storage) as session:
            _, created = await submit_message_in_transaction(
                session, service.runtime, actor, tenant.workspace_id, thread_id, message, request_key="app:test"
            )
            assert created
            raise ValueError("rollback")
    assert len(await runs_kit.inbox(service, thread_id)) == 1
    result, created = await submit_message(
        service.runtime, actor, tenant.workspace_id, thread_id, message, request_key="app:test"
    )
    assert created
    async with transaction(service.runtime.storage) as session:
        replay, created = await submit_message_in_transaction(
            session, service.runtime, actor, tenant.workspace_id, thread_id, message, request_key="app:test"
        )
        assert not created and replay.entry.id == result.entry.id
    viewer = Principal(tenant.principal_id, "user", (Grant(tenant.organization_id, None, BUILT_IN_ROLES["viewer"]),))
    with pytest.raises(ServiceError):
        await submit_message(service.runtime, viewer, tenant.workspace_id, thread_id, message, request_key="app:test")


async def test_followup_guard_and_submit_share_thread_lock(service, scripted_model, runs_kit):
    from a13n_service.runs.inbox import pending_entries
    from a13n_service.runs.schemas import Message
    from a13n_service.runs.submit import submit_message_in_transaction
    from a13n_service.runs.tables import RunRow
    from a13n_service.runs.threads import get_thread
    from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal

    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    first = await runs_kit.start_thread(service, agent, "first")
    thread_id, run_id = first["thread"]["id"], first["run"]["id"]
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    tenant = service.tenant
    actor = Principal(tenant.principal_id, "user", (Grant(tenant.organization_id, None, BUILT_IN_ROLES["admin"]),))
    message = Message.model_validate(runs_kit.message(agent, "follow up", delivery="next_run"))

    async def followup():
        async with transaction(service.runtime.storage) as session:
            thread = await get_thread(session, tenant.workspace_id, thread_id, lock="acceptance")
            last = await session.get_one(RunRow, run_id)
            if (
                thread.last_run_id != run_id
                or thread.current_run_id is not None
                or thread.archived_at is not None
                or last.status != "completed"
                or await pending_entries(session, thread_id, limit=1)
            ):
                return False
            _, created = await submit_message_in_transaction(
                session, service.runtime, actor, tenant.workspace_id, thread_id, message, request_key="followup"
            )
            return created

    assert sorted(await asyncio.gather(followup(), followup())) == [False, True]
    assert len(await runs_kit.inbox(service, thread_id)) == 2


async def test_worker_seal_reaches_control_listener(service, scripted_model, runs_kit, monkeypatch):
    from a13n_service.runs import execute

    async def show_error(runtime, lease, error):
        raise error

    monkeypatch.setattr(execute, "_release", show_error)
    from a13n_service.app_plugin_runtime import app_run_seals
    from a13n_service.tenancy.tables import OrganizationRow

    received = asyncio.Queue()

    async def callback(runtime, event):
        await received.put(event)

    app = InstalledApp(AppDefinition("test.seals", "Seals", run_seals=(RunSealSubscription(callback),)), "test", "1")
    catalog = AppCatalog((app.manifest,))
    control, worker = replace(service.runtime, apps=catalog), replace(service.runtime, apps=catalog)
    async with transaction(control.storage) as session:
        organization = await session.get_one(OrganizationRow, service.tenant.organization_id)
        organization.settings = {**organization.settings, "app_plugins": {"enabled": ["test.seals"]}}
    listener = asyncio.create_task(app_run_seals(control, (app,)))
    try:
        async with asyncio.timeout(3):
            while (await control.redis.pubsub_numsub(channel(control)))[0][1] == 0:
                await asyncio.sleep(0.01)
        await runs_kit.pause_sweeps(service)
        agent = await runs_kit.create_agent(service, scripted_model)
        first = await runs_kit.start_thread(service, agent, "first")
        scripted_model.say("Done")
        await (await runs_kit.attempt(service, runtime=worker))
        event = await asyncio.wait_for(received.get(), 3)
        assert event.run_id == first["run"]["id"]
        assert event.status == "completed"
    finally:
        listener.cancel()
        with pytest.raises(asyncio.CancelledError):
            await listener


async def test_callback_timeout_and_waiting_opt_in_do_not_stop_listener(service):
    from a13n_service.app_plugin_runtime import app_run_seals
    from a13n_service.tenancy.tables import OrganizationRow

    calls = asyncio.Queue()
    timed_out = asyncio.Event()

    async def callback(runtime, event):
        if event.status == "waiting":
            try:
                await asyncio.Event().wait()
            finally:
                timed_out.set()
        else:
            await calls.put(event.status)

    sub = RunSealSubscription(callback, timeout=0.05, statuses=frozenset({"waiting", "failed", "cancelled"}))
    app = InstalledApp(AppDefinition("test.seals", "Seals", run_seals=(sub,)), "test", "1")
    runtime = replace(service.runtime, apps=AppCatalog((app.manifest,)))
    async with transaction(runtime.storage) as session:
        session.add(
            OrganizationRow(id="org_timeout", name="Timeout", settings={"app_plugins": {"enabled": [app.app_id]}})
        )
    task = asyncio.create_task(app_run_seals(runtime, (app,)))
    event = RunSealEvent(
        organization_id="org_timeout",
        workspace_id="w",
        session_id="s",
        thread_id="t",
        run_id="r",
        principal_id="p",
        status="waiting",
        labels={},
    )
    try:
        async with asyncio.timeout(3):
            while (await runtime.redis.pubsub_numsub(channel(runtime)))[0][1] == 0:
                await asyncio.sleep(0.01)
        await runtime.redis.publish(channel(runtime), event.model_dump_json())
        await asyncio.wait_for(timed_out.wait(), 2)
        for status in ("failed", "cancelled"):
            await runtime.redis.publish(channel(runtime), event.model_copy(update={"status": status}).model_dump_json())
        assert {await asyncio.wait_for(calls.get(), 2), await asyncio.wait_for(calls.get(), 2)} == {
            "failed",
            "cancelled",
        }
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_pubsub_channels_are_scoped_to_resolved_redis_database(service):
    from redis.asyncio import ConnectionPool, Redis

    original = service.runtime.redis
    kwargs = dict(original.connection_pool.connection_kwargs)
    kwargs["db"] = (int(kwargs.get("db", 0)) + 1) % 16
    other = Redis(connection_pool=ConnectionPool(**kwargs))
    other_runtime = replace(service.runtime, redis=other)
    try:
        assert channel(service.runtime) != channel(other_runtime)
        async with other.pubsub() as listener:
            await listener.subscribe(channel(other_runtime))
            await listener.get_message(timeout=1)
            await original.publish(channel(service.runtime), "not for the other database")
            assert await listener.get_message(ignore_subscribe_messages=True, timeout=0.1) is None
            await original.publish(channel(other_runtime), "same channel crosses databases")
            assert (await listener.get_message(ignore_subscribe_messages=True, timeout=1))[
                "data"
            ] == "same channel crosses databases"
    finally:
        await other.aclose()
        await other.connection_pool.disconnect()
