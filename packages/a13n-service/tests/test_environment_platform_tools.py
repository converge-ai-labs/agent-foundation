"""Run connection admission is atomic, scoped and independent of Thread defaults."""

import asyncio
import json

import pytest
from a13n_harness.tool_execution import StoredToolExecution
from a13n_service.infra.db import transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.attempts import LeaseLost
from a13n_service.runs.claim import claim
from a13n_service.runs.environments.connections import Connect, connect
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.platform_tokens import authenticate, issue
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.runs.tables import AttemptRow, RunRow, ToolExecutionRow
from a13n_service.runs.tool_executions.authority import ControlSnapshot
from a13n_service.runs.tool_executions.routing import Caller
from a13n_service.runs.tool_executions.store import MOUNTS, Store
from sqlalchemy import func, update
from sqlalchemy.exc import DBAPIError

from .environments_support import BACKEND, reserve, start, with_fake_providers
from .platform_support import call, client, rpc
from .test_tool_execution_store import _record, _save

pytestmark = pytest.mark.anyio


@pytest.fixture
async def connected_env(service):
    env = await with_fake_providers(service)
    submitted = await start(env)
    (lease,) = await claim(env.runtime, worker_id="connect-test", worker_build="test", limit=1)
    token = await issue(env.runtime, lease, frozenset({"environments", "execution"}))
    return env, submitted, lease, token


async def run_mounts(env, run_id):
    response = await env.client.get(f"{env.api}/runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()["environment_mounts"]


@pytest.mark.parametrize("persist", [False, True])
async def test_connect_current_run_and_optional_thread(connected_env, persist):
    env, submitted, lease, token = connected_env
    target = await reserve(env, env.template["id"])
    thread_path = f"{env.api}/threads/{lease.thread_id}"
    thread = (await env.client.get(thread_path)).json()
    arguments = {"environment_id": target["id"], "alias": "dev", "persist_to_thread": persist}
    if persist:
        arguments["expected_version"] = f"{thread['id']}:{thread['version']}"
    async with client(env, {"secret": token}) as http:
        discovered = (await rpc(http, "tools/list")).json()["result"]["tools"]
        tool = next(item for item in discovered if item["name"] == "connect_environment")
        assert "next root model request" in tool["description"]
        assert "run_id" not in tool["inputSchema"]["properties"]
        result = await call(http, "connect_environment", **arguments)
        assert "error" not in result, result
        assert result["application"] == "next_model_request"
        assert result["persisted_to_thread"] is persist
        run = await call(http, "get_run", run_id=lease.run_id)
        content = await call(http, "read_content", reference=run["content"]["environment_mounts"])
        assert target["id"] in str(content)
        if persist:
            arguments["expected_version"] = f"{thread['id']}:{result['thread_version']}"
        again = await call(http, "connect_environment", **arguments)
        assert "error" not in again, again
    actual = await run_mounts(env, lease.run_id)
    assert len(actual) == len(submitted["run"]["environment_mounts"]) + 1
    added = next(m for m in actual if m["name"] == "dev")
    assert added["environment_id"] == target["id"] and added["session_version"] == 1
    desired = (await env.client.get(thread_path + "/environments")).json()["items"]
    assert any(m["name"] == "dev" for m in desired) is persist
    assert BACKEND.preparations == []


async def test_conditional_version_and_conflicts_roll_back_both_scopes(connected_env):
    env, _, lease, token = connected_env
    first, second = await reserve(env, env.template["id"]), await reserve(env, env.template["id"])
    before = await run_mounts(env, lease.run_id)
    async with client(env, {"secret": token}) as http:
        for extra in (
            {"persist_to_thread": True},
            {"expected_version": f"{lease.thread_id}:1"},
            {"persist_to_thread": True, "expected_version": f"{lease.thread_id}:0"},
        ):
            result = await call(http, "connect_environment", environment_id=first["id"], alias="dev", **extra)
            assert "error" in result
            assert await run_mounts(env, lease.run_id) == before
        result = await call(http, "connect_environment", environment_id=first["id"], alias="dev")
        assert "error" not in result, result
        thread = (await env.client.get(f"{env.api}/threads/{lease.thread_id}")).json()
        result = await call(
            http,
            "connect_environment",
            environment_id=second["id"],
            alias="dev",
            persist_to_thread=True,
            expected_version=f"{lease.thread_id}:{thread['version']}",
        )
        assert result["error"]["code"] == "conflict"
        assert len(await run_mounts(env, lease.run_id)) == len(before) + 1
        desired = (await env.client.get(f"{env.api}/threads/{lease.thread_id}/environments")).json()["items"]
        assert not any(m["name"] == "dev" for m in desired)


async def test_concurrent_additions_and_promotion_to_thread(connected_env):
    env, _, lease, token = connected_env
    target = await reserve(env, env.template["id"])
    async with client(env, {"secret": token}) as http:
        results = await asyncio.gather(
            *(
                call(http, "connect_environment", environment_id=target["id"], alias=name)
                for name in ("one", "two", "one")
            )
        )
        assert all("error" not in result for result in results), results
        names = [m["name"] for m in await run_mounts(env, lease.run_id)]
        assert names.count("one") == names.count("two") == 1
        thread = (await env.client.get(f"{env.api}/threads/{lease.thread_id}")).json()
        result = await call(
            http,
            "connect_environment",
            environment_id=target["id"],
            alias="one",
            persist_to_thread=True,
            expected_version=f"{lease.thread_id}:{thread['version']}",
        )
        assert result["persisted_to_thread"] is True, result
        assert [m["name"] for m in await run_mounts(env, lease.run_id)] == names


async def test_discovery_filters_and_connection_rechecks_state(connected_env):
    env, _, _, token = connected_env
    target = await reserve(env, env.template["id"], name="Searchable")
    hidden = await reserve(env, env.template["id"], name="Deleted")
    async with transaction(env.runtime.storage) as session:
        await session.execute(
            update(EnvironmentRow)
            .where(EnvironmentRow.id == hidden["id"])
            .values(status="deleted", operation_id=None, operation_started_at=None)
        )
    async with client(env, {"secret": token}) as http:
        page = await call(http, "list_connectable_environments", q="search")
        assert [item["environment_id"] for item in page["items"]] == [target["id"]]
        assert not any(key in page["items"][0] for key in ("endpoint", "token", "handle"))
        assert page["items"][0]["operations_source"] == "configured"
        failed = await call(http, "connect_environment", environment_id=hidden["id"], alias="deleted")
        assert failed["error"]["code"] == "conflict"
        for alias in ("workspace", "builtin-skills", "builtin-instance"):
            failed = await call(http, "connect_environment", environment_id=target["id"], alias=alias)
            assert failed["error"]["code"] == "invalid_argument"
    assert BACKEND.preparations == []


def environment_context(request):
    prefix = "Current Environment mounts (trusted dynamic context):\n"
    blocks = [
        m["content"]
        for m in request["messages"]
        if isinstance(m.get("content"), str) and m["content"].startswith(prefix)
    ]
    return json.loads(blocks[-1][len(prefix) :])


@pytest.mark.parametrize("persist", [False, True])
async def test_next_model_request_can_use_new_environment(skill_service, scripted_model, runs_kit, persist):
    env = skill_service
    await runs_kit.pause_sweeps(env)
    target = await reserve(env, env.skill_template_id)
    agent = await runs_kit.create_agent(
        env,
        scripted_model,
        toolsets={
            "environments": {"enabled": True, "tools": {"connect_environment": {"permission": "allow"}}},
            "execution": {"enabled": True},
            "shell": {"enabled": False},
            "files": {"enabled": True},
        },
    )
    async with runs_kit.platform_runtime(env) as runtime:
        submitted = await runs_kit.start_thread(env, agent, "Connect the extra environment")
        scripted_model.call("get_thread", {"thread_id": submitted["thread"]["id"]}, call_id="read-thread")
        task = await runs_kit.attempt(env, runtime=runtime)
        await scripted_model.request()
        first_request = await scripted_model.request()
        arguments = {"environment_id": target["id"], "alias": "dev", "persist_to_thread": persist}
        if persist:
            thread = json.loads(
                next(m["content"] for m in first_request["messages"] if m.get("tool_call_id") == "read-thread")
            )
            arguments["expected_version"] = f"{thread['id']}:{thread['version']}"
        scripted_model.call("connect_environment", arguments, call_id="connect")
        scripted_model.call(
            "write", {"file_path": "/mnt/dev/proof.txt", "content": "environment-connected"}, call_id="write"
        )
        scripted_model.call("view", {"file_path": "/mnt/dev/proof.txt"}, call_id="view")
        scripted_model.say("Done")
        await task
        run = await runs_kit.get_run(env, submitted["run"]["id"])
        assert run["status"] == "completed", run
        requests = [first_request] + [await scripted_model.request() for _ in range(3)]
        receipt = next(m["content"] for m in requests[1]["messages"] if m.get("tool_call_id") == "connect")
        assert json.loads(receipt).get("status") == "accepted", receipt
        assert "dev" not in [m["name"] for m in environment_context(requests[0])["mounts"]]
        assert "dev" in [m["name"] for m in environment_context(requests[1])["mounts"]]
        assert environment_context(requests[0])["default_mount"] == environment_context(requests[1])["default_mount"]
        output = next(m["content"] for m in requests[3]["messages"] if m.get("tool_call_id") == "view")
        assert "environment-connected" in output, output
        assert not any(t["function"]["name"].startswith("shell_") for r in requests for t in r["tools"])
        followup = await runs_kit.submit(env, submitted["thread"]["id"], runs_kit.message(agent, "Next ordinary Run"))
        assert followup.status_code == 201, followup.text
        scripted_model.say("Done again")
        await (await runs_kit.attempt(env, runtime=runtime))
        following = await scripted_model.request()
        assert ("dev" in [m["name"] for m in environment_context(following)["mounts"]]) is persist


@pytest.mark.parametrize("revocation", ["expired", "cancelled"])
async def test_connect_rechecks_execution_after_authentication(connected_env, revocation):
    env, _, lease, token = connected_env
    target = await reserve(env, env.template["id"])
    credential = await authenticate(env.runtime, token)
    before = await run_mounts(env, lease.run_id)
    async with transaction(env.runtime.storage) as session:
        if revocation == "expired":
            await session.execute(
                update(AttemptRow)
                .where(AttemptRow.id == lease.attempt_id)
                .values(lease_expires_at=AttemptRow.created_at)
            )
        else:
            await session.execute(
                update(RunRow).where(RunRow.id == lease.run_id).values(cancel_requested_at=func.clock_timestamp())
            )
    with pytest.raises(ServiceError):
        await connect(env.runtime, credential.claims, Connect(environment_id=target["id"], alias="late"))
    assert await run_mounts(env, lease.run_id) == before


async def test_database_keeps_existing_mounts_immutable(connected_env):
    env, _, lease, token = connected_env
    target = await reserve(env, env.template["id"])
    async with client(env, {"secret": token}) as http:
        assert "error" not in await call(http, "connect_environment", environment_id=target["id"], alias="dev")
    accepted = await run_mounts(env, lease.run_id)
    for changed in (
        accepted[:-1],
        list(reversed(accepted)),
        [*accepted, accepted[-1]],
        [{**accepted[0], "working_directory": "/changed"}, *accepted[1:]],
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with transaction(env.runtime.storage) as session:
                await session.execute(
                    update(RunRow).where(RunRow.id == lease.run_id).values(environment_mounts=changed)
                )
    assert await run_mounts(env, lease.run_id) == accepted


async def test_retained_execution_keeps_its_original_mount_scope(connected_env):
    env, _, lease, token = connected_env
    target = await reserve(env, env.template["id"])
    original = tuple(EnvironmentMount.model_validate(m) for m in await run_mounts(env, lease.run_id))
    snapshot = MOUNTS.set(original)
    try:
        async with client(env, {"secret": token}) as http:
            assert "error" not in await call(http, "connect_environment", environment_id=target["id"], alias="dev")
        record = _record(lease)
        # A tool admitted after the write but within the same batch still sees the original scope.
        await _save(Store(env.runtime), lease, StoredToolExecution(record))
    finally:
        MOUNTS.reset(snapshot)
    async with transaction(env.runtime.storage) as session:
        execution = await session.get_one(ToolExecutionRow, record.execution_id)
        assert execution.environment_mounts == [m.model_dump(mode="json") for m in original]
        caller = Caller(
            run_id=lease.run_id, attempt_id=lease.attempt_id, worker_id="connect-test", execution_id=record.execution_id
        )
        authority = await ControlSnapshot.load(session, env.runtime, [caller])
        with pytest.raises(LeaseLost):
            authority.authorize(
                caller, thread_id=lease.thread_id, environment_id=target["id"], mount_name="dev", actions=frozenset()
            )
        authority.authorize(
            caller,
            thread_id=lease.thread_id,
            environment_id=original[0].environment_id,
            mount_name=original[0].name,
            actions=frozenset(),
        )


async def test_offline_client_keeps_directory_and_operation_ceilings(connected_env):
    env, _, lease, token = connected_env
    response = await env.client.post(
        f"{env.api}/client-environments",
        json={
            "device_id": "offline-laptop",
            "name": "Offline laptop",
            "default_working_directory": "/project",
            "permission_ceiling": {"operations": ["environment.file.list", "environment.file.read_text"]},
            "declared_capabilities": {"operations": ["environment.file.list", "environment.file.write_text"]},
        },
    )
    assert response.status_code == 201, response.text
    environment_id = response.json()["client"]["environment"]["id"]
    async with client(env, {"secret": token}) as http:
        page = await call(http, "list_connectable_environments", q="Offline laptop")
        assert page["items"][0]["operations"]["operations"] == ["environment.file.list"]
        result = await call(http, "connect_environment", environment_id=environment_id, alias="laptop")
        assert "error" not in result, result
    mount = next(m for m in await run_mounts(env, lease.run_id) if m["name"] == "laptop")
    assert mount["working_directory"] == "/project"
    assert mount["permission_ceiling"]["operations"] == ["environment.file.list"]
    assert not env.runtime.client_connections.entries


async def test_takeover_restores_connections_accepted_before_local_application(connected_env):
    from a13n_service.infra.db import now
    from a13n_service.runs.execute import _plan
    from a13n_service.runs.seal import LeaseExpirer

    env, _, lease, token = connected_env
    target = await reserve(env, env.template["id"])
    async with client(env, {"secret": token}) as http:
        assert "error" not in await call(http, "connect_environment", environment_id=target["id"], alias="dev")
    async with transaction(env.runtime.storage) as session:
        await session.execute(
            update(AttemptRow).where(AttemptRow.id == lease.attempt_id).values(lease_expires_at=AttemptRow.created_at)
        )
    await LeaseExpirer(env.runtime, batch=10)()
    async with transaction(env.runtime.storage) as session:
        run = await session.get_one(RunRow, lease.run_id)
        run.available_at = await now(session)
    (replacement,) = await claim(env.runtime, worker_id="replacement", worker_build="test", limit=1)
    assert replacement.attempt_id != lease.attempt_id
    plan = await _plan(env.runtime, replacement)
    assert any(m.name == "dev" and m.environment_id == target["id"] for m in plan.mounts)
    async with client(env, {"secret": token}) as http:
        assert (await rpc(http, "tools/list")).status_code == 401
