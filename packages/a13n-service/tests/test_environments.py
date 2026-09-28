"""Environments: templates, desired mounts frozen at acceptance, the fenced lifecycle, maintenance and use."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx2
import pytest
from a13n_service.distribution import OSS
from a13n_service.infra.db import transaction
from a13n_service.providers.registry import Registry
from a13n_service.runs.attempts import Lease
from a13n_service.runs.claim import claim as claim_run
from a13n_service.runs.environments import lifecycle
from a13n_service.runs.environments.execution import PreparedMount, open_mounts, prepare_mounts
from a13n_service.runs.environments.external import seal
from a13n_service.runs.environments.lifecycle import Fault, Outcome, advance, claim, perform, publish
from a13n_service.runs.environments.maintenance import maintain_environments
from a13n_service.runs.environments.schemas import MAX_MOUNTS
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.runs.tables import RunRow, ThreadRow
from a13n_service.tenancy.access import principal_for
from a13n_service.tenancy.authorize import ExecutionAuthority, WorkspaceScope
from sqlalchemy import select, text

from .environments_support import (
    BACKEND,
    FAKE,
    act,
    backdate,
    environment,
    follow_up,
    interrupt,
    new_thread,
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


async def test_templates_validate_their_provider_and_recipe(env) -> None:  # type: ignore[no-untyped-def]
    client, templates = env.client, f"{env.api}/environment-templates"
    invalid = await client.post(
        templates,
        json={"name": "Bad", "provider_id": env.provider["id"], "config": {"recipe": {"cpu": 2}}},
    )
    assert invalid.status_code == 400, invalid.text
    path = f"{templates}/{env.template['id']}"
    body = {"config": {"recipe": {"image": "next"}, "stop_after_seconds": 600}}
    assert (await client.patch(path, json=body)).status_code == 428
    stale = await client.patch(path, json=body, headers={"if-match": f'"{env.template["id"]}:0"'})
    assert stale.status_code == 412
    current = (await client.get(path)).headers["etag"]
    updated = await client.patch(path, json=body, headers={"if-match": current})
    assert updated.status_code == 200 and updated.json()["config"]["recipe"] == {"image": "next"}
    disabled = await client.patch(path, json={"enabled": False}, headers={"if-match": updated.headers["etag"]})
    assert disabled.status_code == 200 and not disabled.json()["enabled"]
    # A disabled template refuses the primary sandbox a new thread would reserve: the entry fails in place.
    refused = await start(env)
    assert refused["run"] is None and refused["entry"]["failure"]["code"] == "disabled"


async def test_acceptance_freezes_mounts_and_edits_follow_the_thread_version(env) -> None:  # type: ignore[no-untyped-def]
    client = env.client
    first = await start(env)
    thread_id, run = first["thread"]["id"], first["run"]
    [primary] = run["environment_mounts"]
    assert primary["name"] == "workspace" and primary["working_directory"] is None
    assert (await environment(env, primary["environment_id"]))["status"] == "creating"
    mounts = f"{env.api}/threads/{thread_id}/environments"
    listing = await client.get(mounts)
    assert [item["name"] for item in listing.json()["items"]] == ["workspace"]
    version = listing.headers["etag"]

    other = (await start(env, "another"))["run"]["environment_mounts"][0]["environment_id"]
    body = {"name": "data", "environment_id": other, "working_directory": "/work/data"}
    assert (await client.post(mounts, json=body)).status_code == 428
    assert (await client.post(mounts, json={**body, "working_directory": "/work/../etc"})).status_code == 400
    added = await client.post(mounts, json=body, headers={"if-match": version})
    assert added.status_code == 201 and added.headers["etag"] != version
    assert (await client.post(mounts, json=body, headers={"if-match": version})).status_code == 412
    again = await client.post(mounts, json=body, headers={"if-match": added.headers["etag"]})
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_exists"
    twice = await client.post(mounts, json={**body, "name": "copy"}, headers={"if-match": added.headers["etag"]})
    assert twice.status_code == 409 and reason(twice.json()) == "already_mounted"
    removed = await client.delete(f"{mounts}/data", headers={"if-match": added.headers["etag"]})
    assert removed.status_code == 204
    assert (await client.delete(f"{mounts}/data", headers={"if-match": removed.headers["etag"]})).status_code == 404
    # The accepted run keeps the set frozen at its acceptance.
    frozen = await client.get(f"{env.api}/runs/{run['id']}")
    assert frozen.json()["environment_mounts"] == run["environment_mounts"]


async def test_a_reserved_sandbox_is_created_by_maintenance_and_mounted_by_a_new_thread(env) -> None:  # type: ignore[no-untyped-def]
    client, environments = env.client, f"{env.api}/environments"
    sandbox = await reserve(env, env.template["id"])
    assert (sandbox["status"], sandbox["template_id"], sandbox["name"]) == ("creating", env.template["id"], "Box")
    assert sandbox["device_id"] is None and sandbox["owner_principal_id"] is None
    await maintain_environments(env.runtime, owner="sweep")
    assert (await environment(env, sandbox["id"]))["status"] == "ready"
    assert BACKEND.instances == {sandbox["id"]: "running"}

    # Mounted as `workspace` by a new thread, it replaces the primary sandbox the agent's template would reserve.
    mounts = [{"name": "workspace", "environment_id": sandbox["id"], "working_directory": "/work/app"}]
    submitted = await start(env, environments=mounts)
    assert submitted["run"]["environment_mounts"] == mounts
    listing = await client.get(f"{env.api}/threads/{submitted['thread']['id']}/environments")
    assert listing.json()["items"] == mounts
    assert [item["id"] for item in (await client.get(environments)).json()["items"]] == [sandbox["id"]]

    # Initial mounts take the checks of mount edits, and a refused one stores nothing.
    retired = await reserve(env, env.template["id"])
    code, body = await act(env, "DELETE", retired["id"])
    assert (code, body["status"]) == (202, "deleted")
    for initial, status, refusal in (
        ([{"name": "data", "environment_id": sandbox["id"]}] * 2, 409, "already_exists"),
        (
            [{"name": "data", "environment_id": sandbox["id"]}, {"name": "copy", "environment_id": sandbox["id"]}],
            409,
            "already_mounted",
        ),
        ([{"name": "data", "environment_id": retired["id"]}], 409, "environment_deleted"),
        ([{"name": "data", "environment_id": f"env_{uuid4().hex}"}], 404, "not_found"),
        ([{"name": "data", "environment_id": sandbox["id"], "working_directory": "/work/../etc"}], 400, None),
    ):
        refused = await new_thread(env, "refused", environments=initial)
        error = refused.json()["error"]
        assert refused.status_code == status and refusal in {None, error["code"], error["details"].get("reason")}, error
    assert len((await client.get(f"{env.api}/threads")).json()["items"]) == 1

    # An unmounted sandbox follows its template's idle policy: deleted once idle past `delete_after_seconds`.
    spare = await reserve(env, env.template["id"], name="spare")
    await maintain_environments(env.runtime, owner="sweep")
    assert (await environment(env, spare["id"]))["status"] == "ready"
    template = f"{env.api}/environment-templates/{env.template['id']}"
    etag = (await client.get(template)).headers["etag"]
    policy = {"config": {"delete_after_seconds": 3600}}
    updated = await client.patch(template, json=policy, headers={"if-match": etag})
    assert updated.status_code == 200, updated.text
    await backdate(env, spare["id"], last_used_at=timedelta(hours=2))
    await backdate(env, sandbox["id"], last_used_at=timedelta(hours=2))
    await maintain_environments(env.runtime, owner="sweep")
    assert (await environment(env, spare["id"]))["status"] == "deleted"
    assert (await environment(env, sandbox["id"]))["status"] == "ready", "an accepted run uses it"

    # A disabled template refuses new sandboxes.
    disabled = await client.patch(template, json={"enabled": False}, headers={"if-match": updated.headers["etag"]})
    assert disabled.status_code == 200, disabled.text
    refused = await client.post(environments, json={"template_id": env.template["id"]})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "disabled", refused.text


async def test_a_fork_shares_its_origins_mounts_and_adds_its_own(env, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    # An agent without a template: its runs mount exactly what their threads name.
    agent = await runs_kit.create_agent(env, scripted_model)
    origin = await runs_kit.start_thread(env, agent, "hi")
    scripted_model.say("Hello")
    await (await runs_kit.attempt(env))
    first, second = [(await reserve(env, env.template["id"]))["id"] for _ in range(2)]
    thread = await runs_kit.get_thread(env, origin["thread"]["id"])
    added = await env.client.post(
        f"{env.api}/threads/{thread['id']}/environments",
        json={"name": "data", "environment_id": first},
        headers=runs_kit.if_match(thread),
    )
    assert added.status_code == 201, added.text

    async def fork(text: str, **fields: object) -> httpx2.Response:
        return await env.client.post(
            f"{env.api}/runs/{origin['run']['id']}/fork",
            json=runs_kit.message(agent, text, **fields),
            headers=runs_kit.fresh_key(),
        )

    def mounted(response: httpx2.Response) -> list[tuple[str, str]]:
        assert response.status_code == 201, response.text
        return [(mount["name"], mount["environment_id"]) for mount in response.json()["run"]["environment_mounts"]]

    extra = [{"name": "extra", "environment_id": second}]
    assert mounted(await fork("shared", environments=extra)) == [("data", first), ("extra", second)]
    primary = [{"name": "workspace", "environment_id": second}]
    assert mounted(await fork("fresh", fresh_environments=True, environments=primary)) == [("workspace", second)]
    clash = await fork("clash", environments=[{"name": "data", "environment_id": second}])
    assert clash.status_code == 409 and clash.json()["error"]["code"] == "already_exists", clash.text


async def test_stop_and_delete_wait_for_active_use_and_a_run_restarts_a_stopped_sandbox(env) -> None:  # type: ignore[no-untyped-def]
    submitted = await start(env)
    run_id, environment_id = submitted["run"]["id"], submitted["run"]["environment_mounts"][0]["environment_id"]
    await advance(env.runtime, environment_id, owner="test")
    assert (await environment(env, environment_id))["status"] == "ready"
    assert BACKEND.instances == {environment_id: "running"}

    code, body = await act(env, "POST", f"{environment_id}/stop")
    assert (code, reason(body)) == (409, "in_use")
    code, body = await act(env, "DELETE", environment_id)
    assert (code, reason(body)) == (409, "mounted")

    await interrupt(env, run_id)
    code, body = await act(env, "POST", f"{environment_id}/stop")
    assert (code, body["status"]) == (202, "stopping") and body["operation_id"] is not None
    await advance(env.runtime, environment_id, owner="test")
    assert (await environment(env, environment_id))["status"] == "stopped"
    assert BACKEND.instances == {environment_id: "stopped"}

    # The next run's attempt starts the instance it mounts, then opens it without lifecycle authority.
    follow = await follow_up(env, submitted["thread"]["id"], "again")
    assert follow.status_code == 201, follow.text
    [lease] = await claim_run(env.runtime, worker_id="worker-test", worker_build="test", limit=1)
    mounts = await _prepare(env, lease)
    assert (await environment(env, environment_id))["status"] == "ready"
    assert BACKEND.instances == {environment_id: "running"}
    async with open_mounts(env.runtime, mounts) as opened:
        mount = opened["workspace"]
        assert (mount.mount_path, mount.provider_root, mount.working_directory) == (None, "/work", "/work")
        assert mount.environment.environment_id == environment_id


async def test_a_disabled_provider_still_stops_its_sandboxes(env) -> None:  # type: ignore[no-untyped-def]
    """Lifecycle maintenance reads the provider for no principal, enabled or not."""
    submitted = await start(env)
    run_id, environment_id = submitted["run"]["id"], submitted["run"]["environment_mounts"][0]["environment_id"]
    await advance(env.runtime, environment_id, owner="test")
    await interrupt(env, run_id)
    provider = f"{env.api}/environment-providers/{env.provider['id']}"
    current = (await env.client.get(provider)).headers["etag"]
    disabled = await env.client.patch(provider, json={"enabled": False}, headers={"if-match": current})
    assert disabled.status_code == 200, disabled.text

    code, body = await act(env, "POST", f"{environment_id}/stop")
    assert (code, body["status"]) == (202, "stopping")
    await advance(env.runtime, environment_id, owner="test")
    assert (await environment(env, environment_id))["status"] == "stopped"
    assert BACKEND.instances == {environment_id: "stopped"}


async def test_delete_never_retires_a_sandbox_its_type_cannot_destroy(env) -> None:  # type: ignore[no-untyped-def]
    """Retiring it would leave the instance running outside the Service, so deletion is refused instead."""
    submitted = await start(env)
    environment_id = submitted["run"]["environment_mounts"][0]["environment_id"]
    await advance(env.runtime, environment_id, owner="test")
    await interrupt(env, submitted["run"]["id"])
    await unmount(env, submitted["thread"]["id"])
    env.runtime = replace(env.runtime, registry=Registry.of((*OSS.providers, replace(FAKE, supports_destroy=False))))
    env.app.state.runtime = env.runtime

    code, body = await act(env, "DELETE", environment_id)
    assert (code, reason(body)) == (409, "destroy_unsupported")
    assert (await environment(env, environment_id))["status"] == "ready"
    assert BACKEND.instances == {environment_id: "running"}

    # Nor does its template's idle policy: maintenance only stops it.
    template = f"{env.api}/environment-templates/{env.template['id']}"
    etag = (await env.client.get(template)).headers["etag"]
    policy = {"config": {"delete_after_seconds": 3600}}
    assert (await env.client.patch(template, json=policy, headers={"if-match": etag})).status_code == 200
    await backdate(env, environment_id, last_used_at=timedelta(hours=2))
    await maintain_environments(env.runtime, owner="sweep")
    assert (await environment(env, environment_id))["status"] == "stopped"
    assert BACKEND.instances == {environment_id: "stopped"}


async def test_managed_sandboxes_are_bounded_per_workspace(env) -> None:  # type: ignore[no-untyped-def]
    limits = env.runtime.settings.environments.model_copy(update={"managed_count": 2})
    env.runtime = replace(env.runtime, settings=env.runtime.settings.model_copy(update={"environments": limits}))
    env.app.state.runtime = env.runtime
    reserved = await reserve(env, env.template["id"])
    await start(env)

    refused = await env.client.post(f"{env.api}/environments", json={"template_id": env.template["id"]})
    assert refused.status_code == 409 and reason(refused.json()) == "environment_limit", refused.text
    # A new thread's primary sandbox counts too: its first entry fails in place.
    failed = await start(env, "one more")
    assert failed["run"] is None and failed["entry"]["failure"]["code"] == "environment_limit", failed

    # A deleted instance no longer counts.
    code, body = await act(env, "DELETE", reserved["id"])
    assert (code, body["status"]) == (202, "deleted")
    assert (await reserve(env, env.template["id"]))["status"] == "creating"


async def test_acceptances_that_reserve_and_mount_in_one_workspace_do_not_deadlock(env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A new thread that mounts a sandbox and then reserves its primary, and a message whose run reserves a primary
    and then freezes that sandbox, lock in opposite orders. Neither waits for the other's sandbox lock: new use
    only share-locks a sandbox, and nothing that locks one exclusively reserves."""
    shared = await reserve(env, env.template["id"])
    data = {"name": "data", "environment_id": shared["id"]}
    # Thread Y mounts the shared sandbox and, without its primary, reserves a new one at its next acceptance.
    first = await start(env, "y", environments=[data])
    await interrupt(env, first["run"]["id"])
    await unmount(env, first["thread"]["id"])

    held, release = asyncio.Event(), asyncio.Event()
    original = lifecycle.advisory_lock

    async def holding(session, *key: str) -> None:  # type: ignore[no-untyped-def]
        await original(session, *key)
        if not held.is_set():
            held.set()
            await release.wait()

    monkeypatch.setattr(lifecycle, "advisory_lock", holding)
    body = {"agent_id": env.agent["id"], "payload": {"content": [{"type": "text", "text": "again"}]}}
    message = asyncio.create_task(
        env.client.post(f"{env.api}/threads/{first['thread']['id']}/inbox", json=body, headers={"idempotency-key": "y"})
    )
    async with asyncio.timeout(10):
        await held.wait()
    # While the message's acceptance holds the reservation lock, a new thread that mounts the shared sandbox waits
    # to reserve its own primary.
    created = asyncio.create_task(new_thread(env, "x", environments=[data]))
    async with asyncio.timeout(10):
        while True:
            async with transaction(env.runtime.storage) as session:
                waiting = await session.scalar(
                    text(
                        "SELECT count(*) FROM pg_locks WHERE NOT granted"
                        " AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
                    )
                )
            if waiting:
                break
            await asyncio.sleep(0.02)
    release.set()
    for response in (await message, await created):
        assert response.status_code == 201 and response.json()["run"] is not None, response.text


async def test_new_use_of_a_sandbox_waits_for_no_reservation_and_no_other_use(env) -> None:  # type: ignore[no-untyped-def]
    """A run of a thread that already has its sandbox freezes it while another transaction reserves in the
    workspace and uses the same sandbox."""
    submitted = await start(env)
    thread_id, run_id = submitted["thread"]["id"], submitted["run"]["id"]
    environment_id = submitted["run"]["environment_mounts"][0]["environment_id"]
    await interrupt(env, run_id)
    async with transaction(env.runtime.storage) as session:
        await lifecycle.lock_reservations(session, env.tenant.workspace_id)
        await session.execute(
            select(EnvironmentRow.id).where(EnvironmentRow.id == environment_id).with_for_update(read=True)
        )
        async with asyncio.timeout(10):
            follow = await follow_up(env, thread_id, "again")
    assert follow.status_code == 201 and follow.json()["run"] is not None, follow.text


async def test_a_thread_mounts_a_bounded_number_of_environments(env) -> None:  # type: ignore[no-untyped-def]
    sandboxes = [(await reserve(env, env.template["id"]))["id"] for _ in range(MAX_MOUNTS + 1)]
    initial = [
        {"name": f"data{index}" if index else "workspace", "environment_id": environment_id}
        for index, environment_id in enumerate(sandboxes[:MAX_MOUNTS])
    ]
    extra = {"name": "extra", "environment_id": sandboxes[-1]}
    assert (await new_thread(env, "too many", environments=[*initial, extra])).status_code == 400

    thread_id = (await start(env, environments=initial))["thread"]["id"]
    mounts = f"{env.api}/threads/{thread_id}/environments"
    version = (await env.client.get(mounts)).headers["etag"]
    refused = await env.client.post(mounts, json=extra, headers={"if-match": version})
    assert refused.status_code == 409 and reason(refused.json()) == "mount_limit", refused.text


async def test_a_template_moved_to_another_provider_fails_its_uncreated_sandboxes(env) -> None:  # type: ignore[no-untyped-def]
    """A reservation keeps its provider; the recipe of another provider's template is never built on it."""
    sandbox = await reserve(env, env.template["id"])
    other = await env.client.post(f"{env.api}/environment-providers", json={"type": "fake", "name": "Other"})
    assert other.status_code == 201, other.text
    template = f"{env.api}/environment-templates/{env.template['id']}"
    etag = (await env.client.get(template)).headers["etag"]
    moved = await env.client.patch(template, json={"provider_id": other.json()["id"]}, headers={"if-match": etag})
    assert moved.status_code == 200, moved.text

    await maintain_environments(env.runtime, owner="sweep")
    failure = (await environment(env, sandbox["id"]))["failure"]
    assert (failure["code"], failure["permanent"]) == ("environment_template_moved", True), failure
    assert BACKEND.instances == {} and BACKEND.preparations == []


async def test_a_draining_worker_yields_while_a_sandbox_is_being_created(env, runs_kit) -> None:  # type: ignore[no-untyped-def]
    """Preparing mounts can wait minutes on a provider; a handoff ends the wait without charging an attempt."""
    run_id = (await start(env))["run"]["id"]
    async with asyncio.timeout(10):
        await (await runs_kit.attempt(env, handoff=True))
    assert (await runs_kit.get_run(env, run_id))["status"] == "accepted"
    attempts = (await env.client.get(f"{env.api}/runs/{run_id}/attempts")).json()["items"]
    assert [item["status"] for item in attempts] == ["yielded"]


async def test_each_async_edge_applies_its_own_environment_policy(env, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    """Edges pinning one revision still differ in what their children mount."""
    model = await runs_kit.create_model(env, scripted_model)
    worker = await runs_kit.add_agent(env, "worker", model, instructions="Role: worker")
    pinned = {"agent_id": worker["id"], "revision_id": worker["default_revision_id"]}
    coordinator = await runs_kit.add_agent(
        env,
        "coordinator",
        model,
        instructions="Role: coordinator",
        default_environment_template_id=env.template["id"],
        subagent_mode="async",
        subagents={
            "researcher": {**pinned, "environment": {"mode": "none"}},
            "builder": {**pinned, "environment": {"mode": "shared"}},
            "tester": {**pinned, "environment": {"mode": "dedicated", "template_id": env.template["id"]}},
        },
    )
    for name in ("researcher", "builder", "tester"):
        delegate = {"subagent_name": name, "prompt": "go"}
        scripted_model.call("delegate", delegate, call_id=f"call_{name}", to="Role: coordinator")
    scripted_model.say("Delegated", to="Role: coordinator")
    submitted = await runs_kit.start_thread(env, coordinator, "split the work")
    [primary] = submitted["run"]["environment_mounts"]
    await advance(env.runtime, primary["environment_id"], owner="test")
    await (await runs_kit.attempt(env))
    assert (await runs_kit.get_run(env, submitted["run"]["id"]))["status"] == "completed"

    async with transaction(env.runtime.storage) as session:
        spawned = await session.scalars(select(ThreadRow).where(ThreadRow.origin_run_id == submitted["run"]["id"]))
        children = {child.subagent: child.id for child in spawned}

    async def mounted(thread_id: str) -> list[dict]:
        return (await env.client.get(f"{env.api}/threads/{thread_id}/environments")).json()["items"]

    assert await mounted(children["researcher"]) == []
    assert await mounted(children["builder"]) == [primary]
    [dedicated] = await mounted(children["tester"])
    assert dedicated["name"] == "workspace" and dedicated["environment_id"] != primary["environment_id"]


async def _prepare(env: SimpleNamespace, lease: Lease) -> list[PreparedMount]:
    async with transaction(env.runtime.storage) as session:
        run = await session.get(RunRow, lease.run_id)
        assert run is not None
        scope = WorkspaceScope(run.organization_id, run.workspace_id)
        principal = await principal_for(session, env.runtime.access, run.principal_id, confinement=scope)
        authority = ExecutionAuthority.model_validate(run.authority)
        mounts = [EnvironmentMount.model_validate(mount) for mount in run.environment_mounts]
    return await prepare_mounts(env.runtime, lease, principal, authority, mounts)


async def test_an_uncertain_operation_is_continued_never_replaced(env, caplog) -> None:  # type: ignore[no-untyped-def]
    submitted = await start(env)
    environment_id = submitted["run"]["environment_mounts"][0]["environment_id"]
    await interrupt(env, submitted["run"]["id"])
    thread = submitted["thread"]
    await unmount(env, thread["id"])

    # The instance is created, but the answer is lost: only the same operation may continue.
    BACKEND.lose_response = True
    await advance(env.runtime, environment_id, owner="test")
    lost = await environment(env, environment_id)
    assert lost["status"] == "creating" and lost["failure"]["certainty"] == "unknown"
    assert not lost["failure"]["permanent"] and BACKEND.instances == {environment_id: "running"}
    [failure] = [record for record in caplog.records if record.getMessage() == "Environment operation failed"]
    assert failure.environment_id == environment_id
    assert failure.operation_id == lost["failure"]["operation_id"]
    assert failure.phase == "creating"
    assert failure.exception_details[0]["frames"]
    assert failure.exc_info is None
    code, body = await act(env, "DELETE", environment_id)
    assert (code, reason(body)) == (409, "operation_unresolved")

    # A dispatcher claims the operation, calls the provider and dies before publishing.
    crashed = await claim(env.runtime, environment_id, owner="crashed")
    assert crashed is not None
    await perform(env.runtime, crashed)
    assert await claim(env.runtime, environment_id, owner="early") is None
    await backdate(env, environment_id, lease_expires_at=timedelta(seconds=1))
    await advance(env.runtime, environment_id, owner="recovery")
    ready = await environment(env, environment_id)
    assert ready["status"] == "ready" and ready["failure"] is None
    assert set(BACKEND.preparations) == {lost["operation_id"]} and len(BACKEND.preparations) == 3
    assert BACKEND.instances == {environment_id: "running"}

    # The crashed dispatcher's late answer is fenced out.
    await publish(env.runtime, crashed, Outcome(None, Fault("late", "late", "known", permanent=True)))
    assert (await environment(env, environment_id))["failure"] is None

    code, body = await act(env, "DELETE", environment_id)
    assert (code, body["status"]) == (202, "deleting")
    await advance(env.runtime, environment_id, owner="test")
    assert (await environment(env, environment_id))["status"] == "deleted" and BACKEND.instances == {}
    mounts = f"{env.api}/threads/{thread['id']}/environments"
    version = (await env.client.get(mounts)).headers["etag"]
    body = {"name": "old", "environment_id": environment_id}
    refused = await env.client.post(mounts, json=body, headers={"if-match": version})
    assert refused.status_code == 409 and reason(refused.json()) == "environment_deleted"


async def test_maintenance_stops_idle_sandboxes_and_deletes_unmounted_ones(env) -> None:  # type: ignore[no-untyped-def]
    submitted = await start(env)
    environment_id = submitted["run"]["environment_mounts"][0]["environment_id"]
    await maintain_environments(env.runtime, owner="sweep")
    assert (await environment(env, environment_id))["status"] == "ready"

    await backdate(env, environment_id, last_used_at=timedelta(hours=2))
    await maintain_environments(env.runtime, owner="sweep")
    assert (await environment(env, environment_id))["status"] == "ready", "an accepted run still uses it"

    await interrupt(env, submitted["run"]["id"])
    await maintain_environments(env.runtime, owner="sweep")
    assert (await environment(env, environment_id))["status"] == "stopped"
    assert BACKEND.instances == {environment_id: "stopped"}

    template = f"{env.api}/environment-templates/{env.template['id']}"
    etag = (await env.client.get(template)).headers["etag"]
    policy = {"config": {"delete_after_seconds": 3600}}
    assert (await env.client.patch(template, json=policy, headers={"if-match": etag})).status_code == 200
    await maintain_environments(env.runtime, owner="sweep")
    assert (await environment(env, environment_id))["status"] == "stopped", "the thread still mounts it"
    await unmount(env, submitted["thread"]["id"])
    await maintain_environments(env.runtime, owner="sweep")
    assert (await environment(env, environment_id))["status"] == "deleted" and BACKEND.instances == {}


async def test_authorization_and_private_devices(env) -> None:  # type: ignore[no-untyped-def]
    client = env.client
    submitted = await start(env)
    environment_id = submitted["run"]["environment_mounts"][0]["environment_id"]
    thread = submitted["thread"]["id"]
    accounts = f"{env.workspace}/service-accounts"
    bearers = {}
    for role in ("viewer", "runner"):
        account = (await client.post(accounts, json={"name": role, "role": role})).json()
        key = await client.post(f"{accounts}/{account['id']}/keys", json={"name": role})
        bearers[role] = {"authorization": "Bearer " + key.json()["secret"]}

    templates = f"{env.api}/environment-templates"
    body = {"name": "Other", "provider_id": env.provider["id"]}
    assert (await client.post(templates, json=body, headers=bearers["runner"])).status_code == 403
    assert (await client.get(templates, headers=bearers["viewer"])).status_code == 200
    etag = (await client.get(f"{env.api}/environments/{environment_id}")).headers["etag"]
    stop = await client.post(
        f"{env.api}/environments/{environment_id}/stop", headers={**bearers["runner"], "if-match": etag}
    )
    assert stop.status_code == 403

    # An external target registered by the administrator is private to them.
    device = f"env_{uuid4().hex}"
    async with transaction(env.runtime.storage) as session:
        target = EnvironmentRow(
            id=device,
            organization_id=env.tenant.organization_id,
            workspace_id=env.tenant.workspace_id,
            device_id="laptop",
            endpoint="https://laptop.test",
            owner_principal_id=env.tenant.principal_id,
            name="laptop",
            status="ready",
            generation=0,
            created_by_id=env.tenant.principal_id,
        )
        target.token = seal(env.runtime.keys, target, "laptop-token")
        session.add(target)
    assert (await environment(env, device))["device_id"] == "laptop"
    mounts = f"{env.api}/threads/{thread}/environments"
    version = (await client.get(mounts)).headers["etag"]
    attach = {"name": "data", "environment_id": device}
    # The viewer may not run; the runner may, but not on someone else's device, nor start a thread with it.
    for role in ("viewer", "runner"):
        refused = await client.post(mounts, json=attach, headers={**bearers[role], "if-match": version})
        assert refused.status_code == 403, role
    threads = f"{env.api}/threads"
    started = {"agent_id": env.agent["id"], "payload": {"content": [{"type": "text", "text": "use it"}]}}
    refused = await client.post(
        threads, json={**started, "environments": [attach]}, headers={**bearers["runner"], "idempotency-key": "k"}
    )
    assert refused.status_code == 403, refused.text
    assert len((await client.get(threads)).json()["items"]) == 1
    assert (await client.post(mounts, json=attach, headers={"if-match": version})).status_code == 201
    code, body = await act(env, "POST", f"{device}/stop")
    assert (code, reason(body)) == (409, "connect_only")

    # Reserving a sandbox for threads is a run operation, like the reservation acceptance makes.
    body = {"template_id": env.template["id"]}
    viewer = await client.post(f"{env.api}/environments", json=body, headers=bearers["viewer"])
    assert viewer.status_code == 403, viewer.text
    runner = await client.post(f"{env.api}/environments", json=body, headers=bearers["runner"])
    assert runner.status_code == 201 and runner.json()["owner_principal_id"] is None, runner.text


async def test_the_local_provider_keeps_one_directory_per_environment(env, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    client = env.client
    provider = await client.post(f"{env.api}/environment-providers", json={"type": "local", "name": "Local"})
    assert provider.status_code == 201, provider.text
    template = await client.post(
        f"{env.api}/environment-templates",
        json={
            "name": "Local",
            "provider_id": provider.json()["id"],
            "config": {"recipe": {"root": {"path": str(tmp_path)}}},
        },
    )
    assert template.status_code == 201, template.text

    used = (await reserve(env, template.json()["id"], name="used"))["id"]
    unused = (await reserve(env, template.json()["id"], name="unused"))["id"]
    # Never dispatched, so nothing exists to destroy.
    code, body = await act(env, "DELETE", unused)
    assert (code, body["status"]) == (202, "deleted") and not (tmp_path / unused).exists()

    await advance(env.runtime, used, owner="test")
    assert (await environment(env, used))["status"] == "ready" and (tmp_path / used).is_dir()
    code, body = await act(env, "DELETE", used)
    assert (code, body["status"]) == (202, "deleting")
    await advance(env.runtime, used, owner="test")
    assert (await environment(env, used))["status"] == "deleted" and not (tmp_path / used).exists()
