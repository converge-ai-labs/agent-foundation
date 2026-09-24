"""Thread memory mounts: the routes, a new thread's and a fork's mounts, agent defaults at a thread's first
acceptance, a child thread's adopted mounts and the set each run freezes with its cursors."""

from types import SimpleNamespace
from typing import Any

import pytest
from a13n_service.infra.db import transaction
from a13n_service.runs.memories.mounts import inherited_cursors
from a13n_service.runs.tables import RunRow, ThreadRow
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.anyio

DELEGATE = {"subagent_name": "helper", "prompt": "compute"}


async def create_memory(service: SimpleNamespace, key: str) -> dict[str, Any]:
    created = await service.client.post(f"{service.workspace}/memories", json={"key": key, "name": key})
    assert created.status_code == 201, created.text
    return created.json()


def mount(name: str, memory: dict[str, Any], access: str = "write") -> dict[str, str]:
    return {"name": name, "memory_id": memory["id"], "access": access}


def mounted(run: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(item["name"], item["memory_id"], item["access"]) for item in run["memory_mounts"]]


def reason(response: Any) -> str:
    return response.json()["error"]["details"]["reason"]


async def finish(service: SimpleNamespace, model: Any, runs_kit: SimpleNamespace, text: str = "Done") -> None:
    model.say(text)
    await (await runs_kit.attempt(service))


async def test_thread_mounts_are_edited_under_the_thread_version(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    client = service.client
    agent = await runs_kit.create_agent(service, scripted_model)
    team, notes = await create_memory(service, "team"), await create_memory(service, "notes")
    started = await runs_kit.start_thread(service, agent, "hi", memories=[mount("team", team)])
    run = started["run"]
    assert mounted(run) == [("team", team["id"], "write")]
    mounts = f"{service.workspace}/threads/{started['thread']['id']}/memories"
    listing = await client.get(mounts)
    assert listing.json()["items"] == run["memory_mounts"]
    version = listing.headers["etag"]

    body = mount("notes", notes, "read")
    assert (await client.post(mounts, json=body)).status_code == 428
    assert (await client.post(mounts, json={**body, "name": "Notes"}, headers={"if-match": version})).status_code == 400
    added = await client.post(mounts, json=body, headers={"if-match": version})
    assert added.status_code == 201 and added.json() == body and added.headers["etag"] != version
    assert (await client.post(mounts, json=body, headers={"if-match": version})).status_code == 412
    current = {"if-match": added.headers["etag"]}
    again = await client.post(mounts, json={**body, "memory_id": team["id"]}, headers=current)
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_exists", again.text
    twice = await client.post(mounts, json={**body, "name": "copy"}, headers=current)
    assert twice.status_code == 409 and reason(twice) == "already_mounted", twice.text
    missing = await client.post(mounts, json={**body, "name": "gone", "memory_id": "mem_" + "0" * 20}, headers=current)
    assert missing.status_code == 404, missing.text
    removed = await client.delete(f"{mounts}/notes", headers=current)
    assert removed.status_code == 204
    assert (await client.delete(f"{mounts}/notes", headers={"if-match": removed.headers["etag"]})).status_code == 404

    # The accepted run keeps the set frozen at its acceptance, which nothing changes afterwards.
    assert (await runs_kit.get_run(service, run["id"]))["memory_mounts"] == run["memory_mounts"]
    with pytest.raises(DBAPIError, match="accepted run selection is immutable"):
        async with transaction(service.runtime.storage) as session:
            await session.execute(update(RunRow).where(RunRow.id == run["id"]).values(memory_mounts=[]))


async def test_a_thread_mounts_a_bounded_number_of_memories(serve, settings, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    limited = settings.model_copy(update={"memory": settings.memory.model_copy(update={"mounts_per_thread": 2})})
    async with serve(settings=limited) as service:
        agent = await runs_kit.create_agent(service, scripted_model)
        first, second, third = [await create_memory(service, key) for key in ("one", "two", "three")]
        initial = [mount("one", first), mount("two", second)]
        refused = await service.client.post(
            f"{service.workspace}/threads",
            json=runs_kit.message(agent, "hi", memories=[*initial, mount("three", third)]),
            headers=runs_kit.fresh_key(),
        )
        assert refused.status_code == 409 and reason(refused) == "memory_mount_limit", refused.text
        duplicates = await service.client.post(
            f"{service.workspace}/threads",
            json=runs_kit.message(agent, "hi", memories=[mount("one", first), mount("one", second)]),
            headers=runs_kit.fresh_key(),
        )
        assert duplicates.status_code == 400, duplicates.text

        started = await runs_kit.start_thread(service, agent, "hi", memories=initial)
        mounts = f"{service.workspace}/threads/{started['thread']['id']}/memories"
        version = (await service.client.get(mounts)).headers["etag"]
        extra = await service.client.post(mounts, json=mount("three", third), headers={"if-match": version})
        assert extra.status_code == 409 and reason(extra) == "memory_mount_limit", extra.text

        # Agent defaults that would take a thread over the limit at its first acceptance fail the entry.
        model_id = await runs_kit.create_model(service, scripted_model)
        crowded = await runs_kit.add_agent(service, "crowded", model_id, memory_mounts=[mount("three", third)])
        refused = await runs_kit.start_thread(service, crowded, "hi", memories=initial)
        assert refused["run"] is None and refused["entry"]["failure"]["code"] == "memory_mount_limit", refused


async def test_agent_defaults_join_at_a_threads_first_acceptance_only(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    team, notes, own = [await create_memory(service, key) for key in ("team", "notes", "own")]
    model_id = await runs_kit.create_model(service, scripted_model)
    defaults = [mount("team", team), mount("notes", notes, "read")]
    agent = await runs_kit.add_agent(service, "helper", model_id, memory_mounts=defaults)
    # The thread's own mount keeps its name: the default of that name does not join.
    started = await runs_kit.start_thread(service, agent, "hi", memories=[mount("team", own)])
    assert mounted(started["run"]) == [("notes", notes["id"], "read"), ("team", own["id"], "write")]
    await finish(service, scripted_model, runs_kit)

    thread = await runs_kit.get_thread(service, started["thread"]["id"])
    mounts = f"{service.workspace}/threads/{thread['id']}/memories"
    removed = await service.client.delete(f"{mounts}/notes", headers=runs_kit.if_match(thread))
    assert removed.status_code == 204, removed.text
    later = await runs_kit.submit(service, thread["id"], runs_kit.message(agent, "again"))
    assert later.status_code == 201, later.text
    assert mounted(later.json()["run"]) == [("team", own["id"], "write")]


async def test_a_deleted_default_memory_fails_the_entry(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    team = await create_memory(service, "team")
    model_id = await runs_kit.create_model(service, scripted_model)
    agent = await runs_kit.add_agent(service, "helper", model_id, memory_mounts=[mount("team", team)])
    deleted = await service.client.delete(f"{service.workspace}/memories/{team['id']}", headers=runs_kit.if_match(team))
    assert deleted.status_code == 204, deleted.text
    refused = await runs_kit.start_thread(service, agent, "hi")
    assert refused["run"] is None and refused["entry"]["failure"]["code"] == "invalid_argument", refused
    assert "memory_mounts.0.memory_id" in refused["entry"]["failure"]["message"]


async def test_agent_default_mounts_are_validated_when_authored(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    model_id = await runs_kit.create_model(service, scripted_model)
    team = await create_memory(service, "team")
    cases = [
        ([mount("team", {"id": "mem_" + "0" * 20})], "memory_mounts.0.memory_id"),
        ([mount("team", team), mount("team", team)], None),
        ([mount("Team", team)], None),
    ]
    for memory_mounts, field in cases:
        response = await service.client.post(
            f"{service.workspace}/agents",
            json={
                "key": "invalid",
                "name": "Invalid",
                "config": {"model": {"model_id": model_id}, "memory_mounts": memory_mounts},
            },
        )
        assert response.status_code == 400, (memory_mounts, response.text)
        if field is not None:
            assert response.json()["error"]["details"]["field"] == field, response.text


async def test_a_fork_copies_its_origins_mounts_and_adds_its_own(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    team, notes = await create_memory(service, "team"), await create_memory(service, "notes")
    origin = await runs_kit.start_thread(service, agent, "hi", memories=[mount("team", team)])
    await finish(service, scripted_model, runs_kit)

    async def fork(**fields: Any) -> Any:
        return await service.client.post(
            f"{service.workspace}/runs/{origin['run']['id']}/fork",
            json=runs_kit.message(agent, "fork", **fields),
            headers=runs_kit.fresh_key(),
        )

    forked = await fork(memories=[mount("notes", notes, "read")])
    assert forked.status_code == 201, forked.text
    assert mounted(forked.json()["run"]) == [("notes", notes["id"], "read"), ("team", team["id"], "write")]
    clash = await fork(memories=[mount("team", notes)])
    assert clash.status_code == 409 and clash.json()["error"]["code"] == "already_exists", clash.text


async def test_a_child_thread_adopts_its_parents_mounts_and_its_own_defaults(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    team, notes, other, gone = [await create_memory(service, key) for key in ("team", "notes", "other", "gone")]
    worker = {"memory_mounts": [mount("team", other), mount("notes", notes, "read")]}
    coordinator = await runs_kit.delegating(service, scripted_model, "async", worker=worker)
    scripted_model.call("delegate", DELEGATE, call_id="call_d", to="Role: coordinator")
    scripted_model.say("Delegated", to="Role: coordinator")
    memories = [mount("team", team), mount("gone", gone)]
    submitted = await runs_kit.start_thread(service, coordinator, "delegate", memories=memories)
    # Deleted after the parent froze it: the child does not adopt it.
    deleted = await service.client.delete(f"{service.workspace}/memories/{gone['id']}", headers=runs_kit.if_match(gone))
    assert deleted.status_code == 204, deleted.text
    await (await runs_kit.attempt(service))

    async with transaction(service.runtime.storage) as session:
        child = (
            await session.scalars(select(ThreadRow).where(ThreadRow.origin_run_id == submitted["run"]["id"]))
        ).one()
        run = await session.get_one(RunRow, child.current_run_id)
    # The parent's `team` keeps its name; the worker's default of that name does not join, its `notes` does.
    assert [(item["name"], item["memory_id"]) for item in run.memory_mounts] == [
        ("notes", notes["id"]),
        ("team", team["id"]),
    ]


async def test_deleting_a_memory_unmounts_it_and_moves_the_thread_version(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    team = await create_memory(service, "team")
    started = await runs_kit.start_thread(service, agent, "hi", memories=[mount("team", team)])
    mounts = f"{service.workspace}/threads/{started['thread']['id']}/memories"
    before = (await service.client.get(mounts)).headers["etag"]
    deleted = await service.client.delete(f"{service.workspace}/memories/{team['id']}", headers=runs_kit.if_match(team))
    assert deleted.status_code == 204, deleted.text
    after = await service.client.get(mounts)
    assert after.json()["items"] == [] and after.headers["etag"] != before
    assert mounted(await runs_kit.get_run(service, started["run"]["id"])) == [("team", team["id"], "write")]


async def test_a_run_inherits_cursors_of_memories_mounted_under_the_same_name() -> None:
    parent = RunRow(
        memory_mounts=[
            {"name": "team", "memory_id": "mem_a", "access": "write"},
            {"name": "notes", "memory_id": "mem_b", "access": "read"},
            {"name": "docs", "memory_id": "mem_c", "access": "read"},
        ],
        memory_cursors={"mem_a": "7", "mem_b": "3", "mem_c": None},
    )
    mounts = [
        {"name": "team", "memory_id": "mem_a", "access": "read"},
        {"name": "renamed", "memory_id": "mem_b", "access": "read"},
        {"name": "docs", "memory_id": "mem_c", "access": "read"},
        {"name": "new", "memory_id": "mem_d", "access": "write"},
    ]
    assert inherited_cursors(parent, mounts) == {"mem_a": "7", "mem_c": None}
    assert inherited_cursors(None, mounts) == {}


async def test_archiving_a_thread_removes_its_memory_mounts(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    team, notes = await create_memory(service, "team"), await create_memory(service, "notes")
    started = await runs_kit.start_thread(service, agent, "hi", memories=[mount("team", team)])
    thread = await runs_kit.get_thread(service, started["thread"]["id"])
    archived = await service.client.post(
        f"{service.workspace}/threads/{thread['id']}/archive", headers=runs_kit.if_match(thread)
    )
    assert archived.status_code == 200, archived.text
    mounts = f"{service.workspace}/threads/{thread['id']}/memories"
    listing = await service.client.get(mounts)
    assert listing.json()["items"] == []
    refused = await service.client.post(
        mounts, json=mount("notes", notes), headers={"if-match": listing.headers["etag"]}
    )
    assert refused.status_code == 409 and reason(refused) == "archived", refused.text
