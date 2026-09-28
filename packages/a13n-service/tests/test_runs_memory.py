"""Runs with mounted file memories: context at a run's first input, the tools a mount offers, the cursors each
checkpoint commits, recovery from them, and the per-call recheck of the run's principal."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from a13n_harness.capabilities import MemoryCursors
from a13n_harness.providers.memory import MemoryStoreError, Origin
from a13n_service.infra.db import short_session, transaction
from a13n_service.resources.memories.schemas import MemoryMount
from a13n_service.resources.memories.tables import MemoryFileRevisionRow
from a13n_service.runs.attempts import Lease
from a13n_service.runs.boundaries import Boundaries
from a13n_service.runs.memories.execution import PlannedMemory, file_memory
from a13n_service.runs.seal import expire_leases
from a13n_service.runs.tables import AttemptRow, RunRow
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, ExecutionAuthority, Grant, Principal
from sqlalchemy import select, update

pytestmark = pytest.mark.anyio

BLOCK = '<memory-context memory="team"'
FULL = '<memory-context memory="team" trust="untrusted" kind="full">'
CHANGES = '<memory-context memory="team" trust="untrusted" kind="changes">'


async def team_memory(service: SimpleNamespace) -> dict[str, Any]:
    """A memory whose always-loaded README leads its context, with one more file in its index."""
    created = await service.client.post(f"{service.api}/memories", json={"name": "Team", "always_load": ["README.md"]})
    assert created.status_code == 201, created.text
    memory = created.json()
    for path, content in (("README.md", "# Team\nIndent with tabs.\n"), ("notes/tea.md", "Oolong\n")):
        await write_file(service, memory, path, content)
    return memory


async def write_file(service: SimpleNamespace, memory: dict[str, Any], path: str, content: str) -> None:
    created = await service.client.post(
        f"{service.api}/memories/{memory['id']}/files", json={"path": path, "content": content}
    )
    assert created.status_code == 201, created.text


def mount(memory: dict[str, Any], access: str = "write") -> dict[str, str]:
    return {"name": "team", "memory_id": memory["id"], "access": access}


def text(request: dict[str, Any]) -> str:
    """Every text part of the request's messages."""
    parts: list[str] = []
    for message in request["messages"]:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(part.get("text", "") for part in content if isinstance(part, dict))
    return "\n".join(parts)


def tool_names(request: dict[str, Any]) -> set[str]:
    return {tool["function"]["name"] for tool in request.get("tools", [])}


def tool_result(request: dict[str, Any], call_id: str) -> str:
    [content] = [message["content"] for message in request["messages"] if message.get("tool_call_id") == call_id]
    return content


async def cursors(service: SimpleNamespace, run_id: str) -> dict[str, str | None]:
    async with short_session(service.runtime.storage) as session:
        return (await session.get_one(RunRow, run_id)).memory_cursors


async def test_each_run_gets_context_as_of_the_cursor_its_thread_committed(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    memory = await team_memory(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    coffee = {"memory": "team", "path": "notes/coffee.md", "content": "Flat white\n"}
    scripted_model.call("memory_file_create", coffee, call_id="call_create")
    scripted_model.say("Saved")
    started = await runs_kit.start_thread(service, agent, "remember my coffee", memories=[mount(memory)])
    first_run, thread_id = started["run"]["id"], started["thread"]["id"]
    await (await runs_kit.attempt(service))

    first, second = await scripted_model.request(), await scripted_model.request()
    assert FULL in text(first) and "Indent with tabs." in text(first) and "notes/tea.md" in text(first)
    assert {f"memory_file_{key}" for key in ("view", "grep", "create", "edit", "append", "move", "delete")} <= (
        tool_names(first)
    )
    # The context is part of the input's history; the tool results' request carries no new context.
    assert text(second).count(BLOCK) == 1
    assert (await runs_kit.get_run(service, first_run))["status"] == "completed"
    async with short_session(service.runtime.storage) as session:
        revision = (
            await session.scalars(
                select(MemoryFileRevisionRow).where(
                    MemoryFileRevisionRow.memory_id == memory["id"], MemoryFileRevisionRow.path == "notes/coffee.md"
                )
            )
        ).one()
    assert (revision.op, revision.run_id, revision.tool_call_id) == ("create", first_run, "call_create")
    assert revision.principal_id == service.tenant.principal_id
    # The cursor the run delivered: its context covered the two files before its own write.
    assert await cursors(service, first_run) == {memory["id"]: "2"}

    # The next run lists what changed since: the run's own write and another conversation's.
    await write_file(service, memory, "notes/juice.md", "Orange\n")
    scripted_model.say("Noted")
    later = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "what changed?"))
    second_run = later.json()["run"]["id"]
    assert await cursors(service, second_run) == {memory["id"]: "2"}
    await (await runs_kit.attempt(service))
    changed = text(await scripted_model.request())
    assert changed.count(BLOCK) == 2 and CHANGES in changed
    delta = changed[changed.index(CHANGES) :]
    assert "notes/coffee.md" in delta and "notes/juice.md" in delta and "notes/tea.md" not in delta
    assert await cursors(service, second_run) == {memory["id"]: "4"}

    # Nothing changed since: the run gets no memory context at all.
    scripted_model.say("Same")
    third = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "anything new?"))
    await (await runs_kit.attempt(service))
    assert text(await scripted_model.request()).count(BLOCK) == 2
    assert await cursors(service, third.json()["run"]["id"]) == {memory["id"]: "4"}


async def test_a_mount_and_the_toolset_limit_the_offered_tools(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    memory = await team_memory(service)
    model = await runs_kit.create_model(service, scripted_model)
    no_grep = {"memory": {"tools": {"file_grep": {"enabled": False}}}}
    reading = await runs_kit.add_agent(service, "reader", model, toolsets=no_grep)
    scripted_model.say("Read")
    await runs_kit.start_thread(service, reading, "look", memories=[mount(memory, "read")])
    await (await runs_kit.attempt(service))
    request = await scripted_model.request()
    assert {name for name in tool_names(request) if name.startswith("memory_")} == {"memory_file_view"}

    # Without the toolset the run still gets its memories' context, but no tool.
    silent = await runs_kit.add_agent(service, "silent", model, toolsets={"memory": {"enabled": False}})
    scripted_model.say("Quiet")
    await runs_kit.start_thread(service, silent, "look", memories=[mount(memory, "write")])
    await (await runs_kit.attempt(service))
    request = await scripted_model.request()
    assert FULL in text(request) and not any(name.startswith("memory_") for name in tool_names(request))


async def test_tool_permissions_apply_to_the_memory_tools(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    memory = await team_memory(service)
    rules = {"memory": {"tools": {"file_delete": {"permission": "deny"}, "file_append": {"permission": "ask"}}}}
    model = await runs_kit.create_model(service, scripted_model)
    agent = await runs_kit.add_agent(service, "careful", model, toolsets=rules)
    tea = {"memory": "team", "path": "notes/tea.md"}
    scripted_model.call("memory_file_delete", {**tea, "version": "1"}, call_id="call_delete")
    scripted_model.call("memory_file_append", {**tea, "content": "Sencha\n"}, call_id="call_append")
    started = await runs_kit.start_thread(service, agent, "tidy up", memories=[mount(memory)])
    await (await runs_kit.attempt(service))
    run = await runs_kit.get_run(service, started["run"]["id"])
    # The denied delete never reached the store; the append waits for approval.
    assert (run["status"], run["wait_reason"]) == ("waiting", "approval"), run
    assert [item["tool_name"] for item in run["pending"]["approvals"]] == ["memory_file_append"]
    tea_file = await service.client.get(f"{service.api}/memories/{memory['id']}/files/notes/tea.md")
    assert tea_file.json()["content"] == "Oolong\n"


async def test_only_the_root_agent_gets_the_runs_memories(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    memory = await team_memory(service)
    agent = await runs_kit.delegating(service, scripted_model, "inline")
    delegate = {"subagent": "helper", "prompt": "compute"}
    scripted_model.call("delegate", delegate, call_id="call_d", to="Role: coordinator")
    scripted_model.say("42", to="Role: worker")
    scripted_model.say("Done", to="Role: coordinator")
    await runs_kit.start_thread(service, agent, "ask the helper", memories=[mount(memory)])
    await (await runs_kit.attempt(service))
    requests = [scripted_model.requests.get_nowait() for _ in range(scripted_model.requests.qsize())]
    coordinator = [request for request in requests if "Role: coordinator" in text(request)]
    worker = [request for request in requests if "Role: worker" in text(request)]
    assert FULL in text(coordinator[0]) and "memory_file_view" in tool_names(coordinator[0])
    assert worker, requests
    for request in worker:
        assert BLOCK not in text(request) and not any(name.startswith("memory_") for name in tool_names(request))


async def test_a_memory_deleted_mid_run_refuses_its_next_call(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    memory = await team_memory(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    gate = asyncio.Event()
    scripted_model.call("memory_file_view", {"memory": "team", "path": ""}, call_id="call_view", gate=gate)
    scripted_model.say("Gone")
    await runs_kit.start_thread(service, agent, "look", memories=[mount(memory)])
    running = await runs_kit.attempt(service)
    await scripted_model.request()
    deleted = await service.client.delete(f"{service.api}/memories/{memory['id']}", headers=runs_kit.if_match(memory))
    assert deleted.status_code == 204, deleted.text
    gate.set()
    await running
    assert "memory_deleted" in tool_result(await scripted_model.request(), "call_view")


async def test_a_recovered_attempt_continues_from_the_committed_cursors(
    service, scripted_model, runs_kit, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    memory = await team_memory(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    scripted_model.call("memory_file_view", {"memory": "team", "path": "notes/tea.md"}, call_id="call_view")
    run_id = (await runs_kit.start_thread(service, agent, "look", memories=[mount(memory)]))["run"]["id"]
    committed = asyncio.Event()
    before = Boundaries.before_tool_execute

    async def stop_after_commit(self, ctx, *, call, tool_def, args):  # type: ignore[no-untyped-def]
        args = await before(self, ctx, call=call, tool_def=tool_def, args=args)
        committed.set()
        await asyncio.Event().wait()
        return args

    monkeypatch.setattr(Boundaries, "before_tool_execute", stop_after_commit)
    running = await runs_kit.attempt(service)
    try:
        async with asyncio.timeout(10):
            await committed.wait()
    finally:
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    monkeypatch.setattr(Boundaries, "before_tool_execute", before)
    # The tool boundary committed the history holding the context together with the cursor it delivered.
    assert await cursors(service, run_id) == {memory["id"]: "2"}

    await write_file(service, memory, "notes/juice.md", "Orange\n")
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(AttemptRow).where(AttemptRow.run_id == run_id).values(lease_expires_at=AttemptRow.created_at)
        )
    await expire_leases(service.runtime, batch=10)
    async with transaction(service.runtime.storage) as session:
        await session.execute(update(RunRow).where(RunRow.id == run_id).values(available_at=RunRow.created_at))
    scripted_model.say("Recovered")
    await (await runs_kit.attempt(service))
    result = await runs_kit.get_run(service, run_id)
    assert (result["status"], result["attempts"]) == ("completed", 2), result
    await scripted_model.request()
    recovered = text(await scripted_model.request())
    # The recovered attempt continues the committed history: its context is not delivered again.
    assert recovered.count(BLOCK) == 1 and "notes/juice.md" not in recovered
    assert await cursors(service, run_id) == {memory["id"]: "2"}


async def test_store_calls_recheck_the_run_principal_under_its_authority(service) -> None:  # type: ignore[no-untyped-def]
    memory = await team_memory(service)
    tenant = service.tenant
    viewer = Principal(
        tenant.principal_id, "user", (Grant(tenant.organization_id, tenant.workspace_id, BUILT_IN_ROLES["viewer"]),)
    )
    authority = ExecutionAuthority(
        principal_id=tenant.principal_id,
        organization_id=tenant.organization_id,
        workspace_id=tenant.workspace_id,
        verbs=frozenset({"read", "run"}),
    )
    run = RunRow(
        id="run_0123456789abcdef0123456789",
        organization_id=tenant.organization_id,
        workspace_id=tenant.workspace_id,
        principal_id=tenant.principal_id,
    )
    planned = (PlannedMemory(MemoryMount(name="team", memory_id=memory["id"], access="write"), None, ()),)
    capability = file_memory(
        service.runtime.storage,
        service.runtime.settings.memory,
        planned,
        lease=Lease(run.id, "rat_test", "thr_test", run.organization_id, run.workspace_id, 1, "worker", "token"),
        principal=viewer,
        authority=authority,
        cursors=MemoryCursors(),
        tools=("view",),
    )
    assert capability is not None
    [store] = [mount.store for mount in capability.mounts]
    assert (await store.read("notes/tea.md")).text == "Oolong\n"
    with pytest.raises(MemoryStoreError) as refused:
        await store.write("notes/new.md", "x\n", expected=None, origin=Origin(run_id=run.id))
    assert refused.value.code == "forbidden"
