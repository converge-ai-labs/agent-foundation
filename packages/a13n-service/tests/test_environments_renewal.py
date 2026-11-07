"""Renewal: the ready sandboxes of a type that ends them unless renewed, kept alive by their own sweep."""

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory, provider_error
from a13n_service.distribution import OSS
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import now, transaction
from a13n_service.runs.environments import lifecycle, mounts, renewal
from a13n_service.runs.environments.lifecycle import advance
from a13n_service.runs.environments.maintenance import maintain_environments, renew_environments, renewal_sweep
from a13n_service.runs.environments.tables import EnvironmentRow
from sqlalchemy import func, select, text, update

from .environments_support import (
    BACKEND,
    DEFAULT_SESSION,
    act,
    backdate,
    environment,
    follow_up,
    interrupt,
    reason,
    reserve,
    start,
    unmount,
    with_fake_providers,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
async def env(service) -> SimpleNamespace:  # type: ignore[no-untyped-def]
    return await with_fake_providers(service)


async def expiring_template(env: SimpleNamespace, **provider: object) -> str:
    """A template of the expiring type, on its own provider."""
    created = await env.client.post(
        f"{env.api}/environment-providers",
        json={"type": "expiring", "name": "Expiring", **provider},
    )
    assert created.status_code == 201, created.text
    template = await env.client.post(
        f"{env.api}/environment-templates",
        json={"name": "Expiring", "provider_id": created.json()["id"]},
    )
    assert template.status_code == 201, template.text
    return template.json()["id"]


async def ready_sandbox(env: SimpleNamespace, **provider: object) -> str:
    """A ready sandbox of the expiring type, renewed once, so its expiry is known."""
    environment_id = (await reserve(env, await expiring_template(env, **provider)))["id"]
    await maintain_environments(env.runtime, owner="sweep")
    await renew_environments(env.runtime)
    assert BACKEND.renewals == [environment_id]
    return environment_id


async def schedule(env: SimpleNamespace, environment_id: str) -> tuple[datetime | None, datetime | None]:
    """When the next renewal is due, and the expiry the provider last reported."""
    async with transaction(env.runtime.storage) as session:
        query = select(EnvironmentRow.renew_at, EnvironmentRow.expires_at).where(EnvironmentRow.id == environment_id)
        renew_at, expires_at = (await session.execute(query)).one()
    return renew_at, expires_at


async def due_in(env: SimpleNamespace, environment_id: str) -> timedelta:
    """How long until the next renewal is due."""
    async with transaction(env.runtime.storage) as session:
        query = select(EnvironmentRow.renew_at - func.now()).where(EnvironmentRow.id == environment_id)
        due = await session.scalar(query)
    assert due is not None
    return due


async def due_now(env: SimpleNamespace, environment_id: str) -> None:
    await backdate(env, environment_id, renew_at=timedelta(seconds=1))


async def age_failure(env: SimpleNamespace, environment_id: str, age: timedelta) -> None:
    """Move the recorded failure's first occurrence into the past, as it lasting would."""
    at = text("jsonb_set(failure, '{at}', to_jsonb(now() - make_interval(secs => :age)))")
    at = at.bindparams(age=age.total_seconds())
    async with transaction(env.runtime.storage) as session:
        await session.execute(update(EnvironmentRow).where(EnvironmentRow.id == environment_id).values(failure=at))


async def handle(env: SimpleNamespace, environment_id: str) -> dict:
    async with transaction(env.runtime.storage) as session:
        value = await session.scalar(select(EnvironmentRow.handle).where(EnvironmentRow.id == environment_id))
    assert value is not None
    return value


async def restart(env: SimpleNamespace, environment_id: str) -> None:
    """Start a stopped instance as a waiting attempt would."""
    async with transaction(env.runtime.storage) as session:
        row = await session.get(EnvironmentRow, environment_id, with_for_update=True)
        assert row is not None and row.status == "stopped"
        await lifecycle.begin(session, row, "starting")
    await advance(env.runtime, environment_id, owner="test")
    assert (await environment(env, environment_id))["status"] == "ready"


async def test_renewals_have_their_own_sweep_and_fall_due_halfway_to_the_expiry(env) -> None:  # type: ignore[no-untyped-def]
    assert renewal_sweep in OSS.sweeps
    expiring = (await reserve(env, await expiring_template(env)))["id"]
    plain = (await reserve(env, env.template["id"]))["id"]
    await maintain_environments(env.runtime, owner="sweep")
    for environment_id in (expiring, plain):
        assert (await environment(env, environment_id))["status"] == "ready"
    # Becoming ready makes the first renewal due at once; a type that never ends sandboxes has none.
    assert await schedule(env, plain) == (None, None)
    assert await due_in(env, expiring) <= timedelta() and (await schedule(env, expiring))[1] is None
    await maintain_environments(env.runtime, owner="sweep")
    assert BACKEND.renewals == [], "maintenance never renews"
    path = f"{env.api}/environments/{expiring}"
    etag = (await env.client.get(path)).headers["etag"]

    async with transaction(env.runtime.storage) as session:
        before = await now(session)
    await renew_environments(env.runtime)
    async with transaction(env.runtime.storage) as session:
        after = await now(session)
    assert BACKEND.renewals == [expiring]
    # Bound the midpoint with the database clock used to publish it; the provider's host clock may differ.
    renew_at, expiry = await schedule(env, expiring)
    assert expiry == BACKEND.expiries[expiring] and renew_at is not None
    assert before + (expiry - before) / 2 <= renew_at <= after + (expiry - after) / 2
    assert (await env.client.get(path)).headers["etag"] == etag, "renewal is invisible to clients"
    await renew_environments(env.runtime)
    assert BACKEND.renewals == [expiring], "not due yet"
    await due_now(env, expiring)
    await renew_environments(env.runtime)
    assert BACKEND.renewals == [expiring] * 2

    # Beginning an operation ends renewal: a stopping or stopped sandbox is never renewed.
    code, body = await act(env, "POST", f"{expiring}/stop")
    assert (code, body["status"]) == (202, "stopping") and await schedule(env, expiring) == (None, None)
    await maintain_environments(env.runtime, owner="sweep")
    await renew_environments(env.runtime)
    assert (await environment(env, expiring))["status"] == "stopped"
    assert await schedule(env, expiring) == (None, None) and BACKEND.renewals == [expiring] * 2

    # Becoming ready again renews it again.
    await restart(env, expiring)
    await renew_environments(env.runtime)
    assert BACKEND.renewals == [expiring] * 3


async def test_an_unclassified_renewal_error_is_locatable_without_leaking_its_payload(env, caplog) -> None:  # type: ignore[no-untyped-def]
    sandbox = await ready_sandbox(env)
    BACKEND.renewal_error = RuntimeError("secret_provider_body")
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    failed = await environment(env, sandbox)
    assert failed["status"] == "ready" and failed["failure"]["code"] == "environment_operation_failed"
    [failure] = [record for record in caplog.records if record.getMessage() == "Environment renewal failed"]
    assert failure.environment_id == sandbox
    assert failure.exception_details[0]["type"] == "builtins.RuntimeError"
    assert failure.exception_details[0]["frames"]
    assert "secret_provider_body" not in json.dumps(vars(failure))
    BACKEND.renewal_error = None
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    assert (await environment(env, sandbox))["failure"] is None


async def test_a_failed_renewal_backs_off_and_one_the_provider_cannot_grant_waits_for_the_expiry(env) -> None:  # type: ignore[no-untyped-def]
    sandbox = await ready_sandbox(env)
    path = f"{env.api}/environments/{sandbox}"

    # A transient failure is recorded without refusing use, and retried halfway to the expiry still known.
    BACKEND.renewal_error = provider_error("fake", "provider_unavailable", EnvironmentProviderErrorCategory.UNAVAILABLE)
    await due_now(env, sandbox)
    async with transaction(env.runtime.storage) as session:
        before = await now(session)
    await renew_environments(env.runtime)
    async with transaction(env.runtime.storage) as session:
        after = await now(session)
    failed = await environment(env, sandbox)
    assert failed["status"] == "ready" and failed["failure"]["code"] == "provider_unavailable"
    assert not failed["failure"]["permanent"] and failed["failure"]["operation_id"] is None
    renew_at, expiry = await schedule(env, sandbox)
    assert renew_at is not None and expiry is not None
    assert before + (expiry - before) / 2 <= renew_at <= after + (expiry - after) / 2
    submitted = await start(env, environments=[{"name": "workspace", "environment_id": sandbox}])
    await interrupt(env, submitted["run"]["id"])

    # Past the expiry, the retry waits as long as the same failure has lasted, at most ten minutes; a repeated
    # failure keeps its first record.
    for lasted, wait in ((timedelta(minutes=3), timedelta(minutes=3)), (timedelta(hours=1), timedelta(minutes=10))):
        await age_failure(env, sandbox, lasted)
        await backdate(env, sandbox, renew_at=timedelta(seconds=1), expires_at=timedelta(seconds=1))
        etag = (await env.client.get(path)).headers["etag"]
        await renew_environments(env.runtime)
        assert (await env.client.get(path)).headers["etag"] == etag
        assert abs(await due_in(env, sandbox) - wait) < timedelta(seconds=10)

    # A failure repeating cannot fix keeps its permanence, and refuses new use until a renewal succeeds.
    BACKEND.renewal_error = provider_error("fake", "provider_spec_invalid", EnvironmentProviderErrorCategory.INVALID)
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    refused = await environment(env, sandbox)
    assert (refused["failure"]["code"], refused["failure"]["permanent"]) == ("provider_spec_invalid", True)
    BACKEND.renewal_error = None
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    assert (await environment(env, sandbox))["failure"] is None

    # A renewal the provider cannot grant, near a vendor's hard lifetime, is expected: nothing is recorded, and the
    # next renewal waits for the reported expiry, when the sandbox is found gone or stopped.
    BACKEND.renewal_error = provider_error(
        "fake", "provider_keepalive_limit", EnvironmentProviderErrorCategory.UNSUPPORTED
    )
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    assert (await environment(env, sandbox))["failure"] is None
    assert await schedule(env, sandbox) == (BACKEND.expiries[sandbox], BACKEND.expiries[sandbox])


async def test_a_stopped_sandbox_waits_for_a_run_and_an_opted_out_session_waits_for_explicit_replacement(env) -> None:  # type: ignore[no-untyped-def]
    sandbox = await ready_sandbox(env)
    submitted = await start(
        env,
        session={"auto_recreate_lost_environment": False},
        environments=[{"name": "workspace", "environment_id": sandbox}],
    )
    thread_id = submitted["thread"]["id"]
    await interrupt(env, submitted["run"]["id"])

    # A provider that stopped the sandbox itself, at its own time limit say, leaves it stopped for a run to start.
    BACKEND.instances[sandbox] = "stopped"
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    stopped = await environment(env, sandbox)
    assert (stopped["status"], stopped["failure"]) == ("stopped", None)
    assert await schedule(env, sandbox) == (None, None)
    await restart(env, sandbox)

    # A provider that no longer has it makes it lost for good: new use and stopping are refused with that reason.
    del BACKEND.instances[sandbox]
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    lost = await environment(env, sandbox)
    assert lost["status"] == "ready" and await schedule(env, sandbox) == (None, None)
    failure = lost["failure"]
    assert (failure["code"], failure["permanent"], failure["certainty"]) == ("environment_lost", True, "known")
    refused = (await follow_up(env, thread_id, "refused")).json()
    assert refused["run"] is None and refused["entry"]["failure"]["code"] == "environment_lost", refused
    code, body = await act(env, "POST", f"{sandbox}/stop")
    assert (code, reason(body)) == (409, "environment_lost")
    # Idle, it is not stopped either, which would clear the failure that refuses its use.
    await backdate(env, sandbox, last_used_at=timedelta(hours=2))
    await maintain_environments(env.runtime, owner="sweep")
    assert (await environment(env, sandbox))["failure"]["code"] == "environment_lost"

    # Deleting it also removes it from the thread, whose next run reserves a new primary sandbox.
    code, body = await act(env, "DELETE", sandbox)
    assert (code, body["status"]) == (202, "deleting")
    await advance(env.runtime, sandbox, owner="test")
    assert (await environment(env, sandbox))["status"] == "deleted"
    replaced = await follow_up(env, thread_id, "replaced")
    assert replaced.status_code == 201, replaced.text
    [mount] = replaced.json()["run"]["environment_mounts"]
    assert mount["name"] == "workspace" and mount["environment_id"] != sandbox


@pytest.mark.parametrize("lazy", [True, False])
async def test_lost_primary_uses_ordinary_reservation_and_preparation(env, scripted_model, runs_kit, monkeypatch, lazy):
    from a13n_service.runs import execute

    template = env.template["id"]
    sandbox = (await reserve(env, template))["id"]
    agent = await runs_kit.create_agent(env, scripted_model)
    first = await runs_kit.start_thread(
        env, agent, "first", environments=[{"name": "workspace", "environment_id": sandbox}]
    )
    thread = first["thread"]["id"]
    scripted_model.say("Started")
    await (await runs_kit.attempt(env))
    assert (await runs_kit.get_run(env, first["run"]["id"]))["status"] == "completed"
    code, _ = await act(env, "POST", f"{sandbox}/stop")
    assert code == 202
    await advance(env.runtime, sandbox, owner="test")
    del BACKEND.instances[sandbox]

    # The Run that first discovers the loss is already bound to the original instance.
    discovery = (await runs_kit.submit(env, thread, runs_kit.message(agent, "use original"))).json()["run"]
    await (await runs_kit.attempt(env))
    assert (await runs_kit.get_run(env, discovery["id"]))["status"] == "failed"
    assert (await environment(env, sandbox))["failure"]["code"] == "environment_lost"

    assert await lifecycle.claim(env.runtime, sandbox, owner="sweep") is None
    path = f"{env.api}/threads/{thread}/inbox"
    body = runs_kit.message(agent, "continue", options={"overrides": {"lazy_environment": lazy}})
    headers = runs_kit.fresh_key()
    receipts = await asyncio.gather(*(env.client.post(path, json=body, headers=headers) for _ in range(2)))
    assert sorted(response.status_code for response in receipts) == [200, 201]
    run = receipts[0].json()["run"]
    assert run["id"] == receipts[1].json()["run"]["id"]
    [mount] = run["environment_mounts"]
    replacement = mount["environment_id"]
    assert replacement != sandbox and BACKEND.instances == {}
    environments = (await env.client.get(f"{env.api}/environments")).json()["items"]
    assert {item["id"] for item in environments} == {sandbox, replacement}
    instance = await environment(env, replacement)
    assert (instance["status"], instance["template_id"]) == ("reserved", template)
    [desired] = (await env.client.get(f"{env.api}/threads/{thread}/environments")).json()["items"]
    view = {key: value for key, value in mount.items() if key not in {"session_version", "session_configuration"}}
    assert desired == {**view, "desktop_enabled": False}
    assert (await runs_kit.get_run(env, first["run"]["id"]))["environment_mounts"][0]["environment_id"] == sandbox

    async with transaction(env.runtime.storage) as session:
        audits = await session.scalars(
            select(AuditEventRow.details).where(
                AuditEventRow.action == "thread_environment.recreate", AuditEventRow.target_id == thread
            )
        )
        assert audits.all() == [
            {"name": "workspace", "previous_environment_id": sandbox, "environment_id": replacement}
        ]

    # Project guidance can touch the primary during Harness execution, even on a text-only turn.
    # Observe before opening the Host's mount sources to distinguish eager preparation from that first use.
    open_mounts = execute.open_mounts
    prepared_before_open = []

    def observe_open(*args, **kwargs):
        prepared_before_open.append(replacement in BACKEND.instances)
        return open_mounts(*args, **kwargs)

    monkeypatch.setattr(execute, "open_mounts", observe_open)
    scripted_model.say("Done")
    await (await runs_kit.attempt(env))
    assert (await runs_kit.get_run(env, run["id"]))["status"] == "completed"
    assert prepared_before_open == [not lazy]
    assert set(BACKEND.instances) == {replacement}


async def test_lost_instance_is_only_replaced_for_the_desired_primary(env, runs_kit):
    sandbox = await ready_sandbox(env)
    mount = {
        "name": "workspace",
        "environment_id": sandbox,
        "working_directory": "/project",
        "permission_ceiling": {"operations": []},
    }
    primary = await start(env, environments=[mount])
    named = await start(env, environments=[{**mount, "name": "data"}])
    for receipt in (primary, named):
        await interrupt(env, receipt["run"]["id"])
    del BACKEND.instances[sandbox]
    await due_now(env, sandbox)
    await renew_environments(env.runtime)

    pinned = runs_kit.message(
        env.agent, "use original", options={"environment": {"mode": "existing", "environment_id": sandbox}}
    )
    refused = await runs_kit.submit(env, primary["thread"]["id"], pinned)
    assert refused.json()["entry"]["failure"]["code"] == "environment_lost"
    refused = await follow_up(env, named["thread"]["id"], "use named mount")
    assert refused.json()["entry"]["failure"]["code"] == "environment_lost"

    restored = await follow_up(env, primary["thread"]["id"], "use workspace")
    [replacement] = restored.json()["run"]["environment_mounts"]
    assert replacement["environment_id"] != sandbox
    assert {**replacement, "environment_id": sandbox} == {**mount, **DEFAULT_SESSION}


async def lose_sandbox(env, sandbox):
    del BACKEND.instances[sandbox]
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    assert (await environment(env, sandbox))["failure"]["code"] == "environment_lost"


async def test_recovering_primary_preserves_offline_client_mount_settings(env):
    sandbox = await ready_sandbox(env)
    registered = await env.client.post(
        f"{env.api}/client-environments",
        json={
            "device_id": "offline-laptop",
            "name": "Laptop",
            "default_working_directory": "/project",
            "permission_ceiling": {"operations": ["environment.file.list", "environment.file.read_text"]},
            "declared_capabilities": {"operations": ["environment.file.list"]},
        },
    )
    assert registered.status_code == 201, registered.text
    client_id = registered.json()["client"]["environment"]["id"]
    receipt = await start(
        env,
        environments=[
            {"name": "workspace", "environment_id": sandbox},
            {"name": "laptop", "environment_id": client_id},
        ],
    )
    await interrupt(env, receipt["run"]["id"])
    await lose_sandbox(env, sandbox)
    followup = await follow_up(env, receipt["thread"]["id"], "continue")
    assert followup.status_code == 201, followup.text
    laptop, workspace = followup.json()["run"]["environment_mounts"]
    assert workspace["name"] == "workspace" and workspace["environment_id"] != sandbox
    assert laptop == {
        **DEFAULT_SESSION,
        "name": "laptop",
        "environment_id": client_id,
        "working_directory": "/project",
        "permission_ceiling": {"operations": ["environment.file.list"]},
    }


async def test_concurrent_recovery_of_a_thread_and_its_fork_reserves_one_successor(env, runs_kit, monkeypatch):
    sandbox = await ready_sandbox(env)
    mount = {
        "name": "workspace",
        "environment_id": sandbox,
        "working_directory": "/project",
        "permission_ceiling": {"operations": []},
    }
    origin = await start(env, environments=[mount])
    await interrupt(env, origin["run"]["id"])
    fork = await env.client.post(
        f"{env.api}/runs/{origin['run']['id']}/fork",
        json=runs_kit.message(env.agent, "fork"),
        headers=runs_kit.fresh_key(),
    )
    assert fork.status_code == 201, fork.text
    child = fork.json()
    assert child["run"]["environment_mounts"] == [{**mount, **DEFAULT_SESSION}]
    await interrupt(env, child["run"]["id"])
    pinned = await start(env, options={"environment": {"mode": "existing", "environment_id": sandbox}})
    await lose_sandbox(env, sandbox)

    # Both own their thread lock. Let the fork select the successor first so its origin FK is checked
    # while the origin is waiting for the same reservation lock.
    origin_ready = asyncio.Event()
    fork_selected = asyncio.Event()
    latest = mounts.latest_replacement

    async def fork_first(*args):
        if not origin_ready.is_set():
            origin_ready.set()
            await fork_selected.wait()
            return await latest(*args)
        successor = await latest(*args)
        fork_selected.set()
        return successor

    monkeypatch.setattr(mounts, "latest_replacement", fork_first)
    async with asyncio.timeout(15), asyncio.TaskGroup() as group:
        original = group.create_task(follow_up(env, origin["thread"]["id"], "continue"))
        await origin_ready.wait()
        forked = group.create_task(follow_up(env, child["thread"]["id"], "continue"))
    responses = [original.result(), forked.result()]
    assert [r.status_code for r in responses] == [201, 201], [r.text for r in responses]
    runs = [r.json()["run"] for r in responses]
    successor = runs[0]["environment_mounts"][0]["environment_id"]
    assert successor != sandbox
    assert [r["environment_mounts"] for r in runs] == [[{**mount, **DEFAULT_SESSION, "environment_id": successor}]] * 2
    async with transaction(env.runtime.storage) as session:
        links = await session.execute(select(EnvironmentRow.id, EnvironmentRow.replaces_environment_id))
        assert set(links) == {(sandbox, None), (successor, sandbox)}
    assert not BACKEND.instances
    # A frozen Run still holds the lost instance after both desired mounts have moved.
    await maintain_environments(env.runtime, owner="sweep")
    assert (await environment(env, sandbox))["failure"]["code"] == "environment_lost"
    await interrupt(env, pinned["run"]["id"])
    await maintain_environments(env.runtime, owner="sweep")
    assert (await environment(env, sandbox))["status"] == "deleted"


async def test_successive_recoveries_transfer_one_quota_slot_and_a_late_thread_follows_the_chain(env):
    settings = env.runtime.settings
    limits = settings.environments.model_copy(update={"managed_count": 1})
    env.app.state.runtime = env.runtime = replace(
        env.runtime, settings=settings.model_copy(update={"environments": limits})
    )
    original = await ready_sandbox(env)
    mount = {"name": "workspace", "environment_id": original}
    active, late = [await start(env, environments=[mount]) for _ in range(2)]
    for r in (active, late):
        await interrupt(env, r["run"]["id"])

    async def at_capacity():
        refused = await env.client.post(f"{env.api}/environments", json={"template_id": env.template["id"]})
        assert refused.status_code == 409 and reason(refused.json()) == "environment_limit", refused.text

    chain = [original]
    for n in range(2):
        await lose_sandbox(env, chain[-1])
        await at_capacity()
        recovered = (await follow_up(env, active["thread"]["id"], f"recover {n}")).json()["run"]
        successor = recovered["environment_mounts"][0]["environment_id"]
        assert successor not in chain
        chain.append(successor)
        await at_capacity()
        await interrupt(env, recovered["id"])
        if n == 0:
            async with transaction(env.runtime.storage) as session:
                row = await session.get_one(EnvironmentRow, successor, with_for_update=True)
                assert row.status == "reserved"
                await lifecycle.begin(session, row, "creating")
            await advance(env.runtime, successor, owner="test")
    # The unmounted intermediate retires. Its link still leads a lagging Thread to the newest instance.
    await maintain_environments(env.runtime, owner="sweep")
    assert (await environment(env, chain[1]))["status"] == "deleted"
    assert (await environment(env, original))["failure"]["code"] == "environment_lost"
    caught_up = (await follow_up(env, late["thread"]["id"], "catch up")).json()["run"]
    assert caught_up["environment_mounts"][0]["environment_id"] == chain[2]
    await maintain_environments(env.runtime, owner="sweep")
    assert (await environment(env, original))["status"] == "deleted"
    exact = await start(env, options={"environment": {"mode": "existing", "environment_id": original}})
    assert exact["run"] is None and exact["entry"]["failure"]["code"] == "environment_lost"
    await at_capacity()


async def test_opted_out_session_keeps_its_lost_mount_after_another_session_recovers(env):
    sandbox = await ready_sandbox(env)
    mount = {"name": "workspace", "environment_id": sandbox}
    off = await start(env, environments=[mount], session={"auto_recreate_lost_environment": False})
    on = await start(env, environments=[mount])
    for r in (off, on):
        await interrupt(env, r["run"]["id"])
    await lose_sandbox(env, sandbox)
    # Even an expired idle-delete timer must preserve the opted-out Thread's binding.
    template_path = f"{env.api}/environment-templates/{(await environment(env, sandbox))['template_id']}"
    template = await env.client.get(template_path)
    patch = await env.client.patch(
        template_path, headers={"if-match": template.headers["etag"]}, json={"config": {"delete_after_seconds": 3600}}
    )
    assert patch.status_code == 200, patch.text
    await backdate(env, sandbox, last_used_at=timedelta(days=1))
    await maintain_environments(env.runtime, owner="sweep")
    assert (await environment(env, sandbox))["failure"]["code"] == "environment_lost"
    restored = (await follow_up(env, on["thread"]["id"], "recover")).json()["run"]
    successor = restored["environment_mounts"][0]["environment_id"]
    await maintain_environments(env.runtime, owner="sweep")
    refused = (await follow_up(env, off["thread"]["id"], "keep original")).json()
    assert refused["run"] is None and refused["entry"]["failure"]["code"] == "environment_lost"
    path = f"{env.api}/threads/{off['thread']['id']}/environments"
    assert (await env.client.get(path)).json()["items"][0]["environment_id"] == sandbox
    session_path = f"{env.api}/sessions/{off['thread']['session_id']}"
    etag = (await env.client.get(session_path)).headers["etag"]
    enabled = await env.client.patch(
        session_path, headers={"if-match": etag}, json={"auto_recreate_lost_environment": True}
    )
    assert enabled.status_code == 200, enabled.text
    joined = (await follow_up(env, off["thread"]["id"], "join")).json()["run"]
    assert joined["environment_mounts"][0]["environment_id"] == successor


async def test_idle_retired_successor_releases_quota_then_reunites_its_sharers(env):
    settings = env.runtime.settings
    limits = settings.environments.model_copy(update={"managed_count": 1})
    env.app.state.runtime = env.runtime = replace(
        env.runtime, settings=settings.model_copy(update={"environments": limits})
    )
    lost_id = await ready_sandbox(env)
    template = (await environment(env, lost_id))["template_id"]
    path = f"{env.api}/environment-templates/{template}"
    etag = (await env.client.get(path)).headers["etag"]
    patch = await env.client.patch(path, headers={"if-match": etag}, json={"config": {"delete_after_seconds": 3600}})
    assert patch.status_code == 200, patch.text
    mount = {
        "name": "workspace",
        "environment_id": lost_id,
        "working_directory": "/project",
        "permission_ceiling": {"operations": []},
    }
    early, late = [await start(env, environments=[mount]) for _ in range(2)]
    for receipt in (early, late):
        await interrupt(env, receipt["run"]["id"])
    await lose_sandbox(env, lost_id)
    first = (await follow_up(env, early["thread"]["id"], "recover")).json()["run"]
    idle_id = first["environment_mounts"][0]["environment_id"]
    await interrupt(env, first["id"])
    await backdate(env, idle_id, last_used_at=timedelta(days=1))
    await maintain_environments(env.runtime, owner="sweep")
    assert (await environment(env, idle_id))["status"] == "deleted"

    # Idle deletion returned the slot. A successor competes with ordinary reservations for it.
    other = (await reserve(env, template))["id"]
    for receipt in (early, late):
        blocked = (await follow_up(env, receipt["thread"]["id"], "full")).json()
        assert blocked["run"] is None and blocked["entry"]["failure"]["code"] == "environment_limit"
    assert (await act(env, "DELETE", other))[0] == 202

    # The late thread still selects the original loss. The early thread's mount names its retired successor.
    responses = await asyncio.gather(*(follow_up(env, r["thread"]["id"], "resume") for r in (late, early)))
    assert [r.status_code for r in responses] == [201, 201], [r.text for r in responses]
    bindings = [r.json()["run"]["environment_mounts"] for r in responses]
    new_id = bindings[0][0]["environment_id"]
    assert new_id not in {lost_id, idle_id}
    assert bindings == [[{**mount, **DEFAULT_SESSION, "environment_id": new_id}]] * 2
    async with transaction(env.runtime.storage) as session:
        assert list(
            await session.scalars(select(EnvironmentRow.id).where(EnvironmentRow.replaces_environment_id == idle_id))
        ) == [new_id]
    exact = await start(env, options={"environment": {"mode": "existing", "environment_id": idle_id}})
    assert exact["run"] is None and exact["entry"]["failure"]["code"] == "environment_deleted"


async def test_a_late_thread_shares_successor_views_but_refuses_a_deleted_successor(env):
    original = await ready_sandbox(env)
    mounts = [{"name": "workspace", "environment_id": original}]
    leader, late, delayed = [await start(env, environments=mounts) for _ in range(3)]
    for r in (leader, late, delayed):
        await interrupt(env, r["run"]["id"])
    await lose_sandbox(env, original)
    run = (await follow_up(env, leader["thread"]["id"], "recover")).json()["run"]
    successor = run["environment_mounts"][0]["environment_id"]
    await interrupt(env, run["id"])
    path = f"{env.api}/threads/{late['thread']['id']}/environments"
    etag = (await env.client.get(path)).headers["etag"]
    added = await env.client.post(path, json={"name": "data", "environment_id": successor}, headers={"if-match": etag})
    assert added.status_code == 201, added.text
    shared = (await follow_up(env, late["thread"]["id"], "share successor")).json()
    assert shared["run"] is not None, shared
    views = shared["run"]["environment_mounts"]
    assert [view["name"] for view in views] == ["data", "workspace"]
    assert all(view["environment_id"] == successor for view in views)
    assert all({key: view[key] for key in DEFAULT_SESSION} == DEFAULT_SESSION for view in views)
    await interrupt(env, shared["run"]["id"])

    etag = (await env.client.get(path)).headers["etag"]
    assert (await env.client.delete(f"{path}/data", headers={"if-match": etag})).status_code == 204
    await unmount(env, leader["thread"]["id"])
    await unmount(env, late["thread"]["id"])
    assert (await act(env, "DELETE", successor))[0] == 202
    # A Thread still pointing at the original loss must not recreate an explicitly deleted successor.
    refused = (await follow_up(env, delayed["thread"]["id"], "use latest")).json()
    assert refused["run"] is None and refused["entry"]["failure"]["code"] == "environment_deleted"
    async with transaction(env.runtime.storage) as session:
        assert set(await session.scalars(select(EnvironmentRow.id))) == {original, successor}


async def test_refused_acceptance_rolls_back_recreation(env):
    primary = await ready_sandbox(env)
    template = (await environment(env, primary))["template_id"]
    other = (await reserve(env, template))["id"]
    await maintain_environments(env.runtime, owner="test")
    desired = [{"name": "workspace", "environment_id": primary}, {"name": "z-data", "environment_id": other}]
    first = await start(env, environments=desired)
    await interrupt(env, first["run"]["id"])
    thread = first["thread"]["id"]
    for instance in (primary, other):
        del BACKEND.instances[instance]
        await due_now(env, instance)
    await renew_environments(env.runtime)

    # The lost named mount refuses the Run even though its primary qualified for a fresh reservation.
    refused = (await follow_up(env, thread, "continue")).json()
    assert refused["run"] is None and refused["entry"]["failure"]["code"] == "environment_lost"
    mounts = (await env.client.get(f"{env.api}/threads/{thread}/environments")).json()["items"]
    assert [(m["name"], m["environment_id"]) for m in mounts] == [("workspace", primary), ("z-data", other)]
    assert {e["id"] for e in (await env.client.get(f"{env.api}/environments")).json()["items"]} == {primary, other}
    async with transaction(env.runtime.storage) as session:
        assert (
            await session.scalar(select(AuditEventRow.id).where(AuditEventRow.action == "thread_environment.recreate"))
            is None
        )


async def test_a_sandbox_a_new_credential_cannot_see_is_not_lost(env) -> None:  # type: ignore[no-untyped-def]
    sandbox = await ready_sandbox(env, credential={"api_key": "first"})
    first = (await handle(env, sandbox))["credential_version"]
    assert first is not None
    provider_id = (await environment(env, sandbox))["provider_id"]
    item = f"{env.api}/environment-providers/{provider_id}"

    async def replace_credential(api_key: str) -> None:
        etag = (await env.client.get(item)).headers["etag"]
        body = {"credential": {"api_key": api_key}}
        assert (await env.client.patch(item, json=body, headers={"if-match": etag})).status_code == 200

    # A credential of another account does not see the sandbox, which may well still exist in the first one.
    await replace_credential("other-account")
    del BACKEND.instances[sandbox]
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    unseen = await environment(env, sandbox)
    assert (unseen["failure"]["code"], unseen["failure"]["permanent"]) == ("provider_credential_changed", False)
    assert (await schedule(env, sandbox))[0] is not None, "renewal goes on"

    selected = await start(env, environments=[{"name": "workspace", "environment_id": sandbox}])
    assert selected["run"]["environment_mounts"][0]["environment_id"] == sandbox
    await interrupt(env, selected["run"]["id"])

    # A credential of the first account, rotated, sees it again; renewing records the credential that did.
    await replace_credential("first-rotated")
    BACKEND.instances[sandbox] = "running"
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    assert (await environment(env, sandbox))["failure"] is None
    rotated = (await handle(env, sandbox))["credential_version"]
    assert rotated not in {None, first}

    # The credential that last reached it no longer finding it means it is lost.
    del BACKEND.instances[sandbox]
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    assert (await environment(env, sandbox))["failure"]["code"] == "environment_lost"


async def test_a_superseded_renewal_publishes_nothing(env) -> None:  # type: ignore[no-untyped-def]
    sandbox = (await reserve(env, await expiring_template(env)))["id"]
    await maintain_environments(env.runtime, owner="sweep")

    # A renewal holds its claim until its deadline; one whose dispatcher died is taken over once it expires.
    crashed = await renewal.claim(env.runtime, sandbox)
    assert crashed is not None and await renewal.claim(env.runtime, sandbox) is None
    await due_now(env, sandbox)
    await renewal.renew(env.runtime, sandbox)
    assert len(BACKEND.renewals) == 1
    expiry = await renewal.perform(env.runtime, crashed)
    assert isinstance(expiry, datetime)
    scheduled = await schedule(env, sandbox)
    await renewal.publish(env.runtime, crashed, expiry)
    assert await schedule(env, sandbox) == scheduled

    # An operation that begins meanwhile supersedes a renewal in flight, whatever it observed.
    await due_now(env, sandbox)
    claimed = await renewal.claim(env.runtime, sandbox)
    assert claimed is not None
    code, body = await act(env, "POST", f"{sandbox}/stop")
    assert (code, body["status"]) == (202, "stopping")
    del BACKEND.instances[sandbox]
    await renewal.publish(env.runtime, claimed, await renewal.perform(env.runtime, claimed))
    stopping = await environment(env, sandbox)
    assert (stopping["status"], stopping["failure"]) == ("stopping", None)
