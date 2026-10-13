"""Session defaults and durable memory-only organization through ordinary Service workers."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from a13n_service.infra.db import transaction
from a13n_service.runs.memories.organization import organize
from a13n_service.runs.memories.organization_tables import SessionMemoryRow
from sqlalchemy import update

pytestmark = pytest.mark.anyio


async def settings(service: SimpleNamespace, session_id: str, **memory: Any) -> dict[str, Any]:
    url = f"{service.api}/sessions/{session_id}"
    before = await service.client.get(url)
    changed = await service.client.patch(url, json={"memory": memory}, headers={"if-match": before.headers["etag"]})
    assert changed.status_code == 200, changed.text
    return changed.json()


async def due(service: SimpleNamespace, session_id: str) -> None:
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(SessionMemoryRow).where(SessionMemoryRow.session_id == session_id).values(due_at=None)
        )


async def history(service: SimpleNamespace, session_id: str) -> list[dict[str, Any]]:
    response = await service.client.get(f"{service.api}/sessions/{session_id}/memory/organizations")
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def saved(service: SimpleNamespace, memory_id: str, path: str = "notes.md") -> None:
    response = await service.client.post(
        f"{service.api}/memories/{memory_id}/files", json={"path": path, "content": "Useful saved fact.\n"}
    )
    assert response.status_code == 201, response.text


async def test_session_defaults_are_dynamic_frozen_and_reuse_retained_memory(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    first = await runs_kit.start_thread(service, agent, "first")
    session_id = first["thread"]["session_id"]
    assert first["run"]["memory_mounts"] == []
    enabled = await settings(service, session_id, enabled=True)
    memory_id = enabled["memory_id"]
    assert memory_id is not None and not enabled["memory"]["auto_organize"]
    assert (await runs_kit.get_run(service, first["run"]["id"]))["memory_mounts"] == []
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    next_run = (await runs_kit.submit(service, first["thread"]["id"], runs_kit.message(agent, "next"))).json()["run"]
    assert next_run["memory_mounts"] == [{"name": "session", "memory_id": memory_id, "access": "write", "recall": True}]
    disabled = await settings(service, session_id, enabled=False)
    assert disabled["memory_id"] == memory_id
    assert (await runs_kit.get_run(service, next_run["id"]))["memory_mounts"] == next_run["memory_mounts"]
    separate = await runs_kit.start_thread(service, agent, "separate")
    assert separate["run"]["memory_mounts"] == []
    assert (await settings(service, session_id, enabled=True))["memory_id"] == memory_id


async def test_explicit_read_binding_wins_over_session_default(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    created = await service.client.post(f"{service.api}/sessions", json={"memory": {"enabled": True}})
    assert created.status_code == 201, created.text
    owner = created.json()
    binding = {"name": "notes", "memory_id": owner["memory_id"], "access": "read", "recall": False}
    started = await runs_kit.start_thread(service, agent, "read", session_id=owner["id"], memories=[binding])
    assert started["run"]["memory_mounts"] == [binding]
    assert not (await service.client.get(f"{service.api}/sessions/{owner['id']}")).json()["memory_status"]["pending"]


async def test_organization_coalesces_and_uses_only_memory_with_fresh_rounds(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    model = await runs_kit.create_model(service, scripted_model)
    agent = await runs_kit.add_agent(service, "Business", model)
    started = await runs_kit.start_thread(service, agent, "Business transcript must stay out")
    session_id = started["thread"]["session_id"]
    enabled = await settings(service, session_id, enabled=True, auto_organize=True, model=model)
    agents = await service.client.get(f"{service.api}/agents")
    assert agents.status_code == 200, agents.text
    organizer = next(item for item in agents.json()["items"] if item["preset_kind"] == "memory_organizer")
    detail = await service.client.get(f"{service.api}/agents/{organizer['id']}")
    assert detail.status_code == 200, detail.text
    filtered = await service.client.get(f"{service.api}/agents", params={"preset_kind": "memory_organizer"})
    assert filtered.status_code == 200 and [item["id"] for item in filtered.json()["items"]] == [organizer["id"]]
    await saved(service, enabled["memory_id"])
    scripted_model.say("Business done")
    await (await runs_kit.attempt(service))
    await scripted_model.request()
    before = (await service.client.get(f"{service.api}/sessions/{session_id}")).json()
    assert before["memory_status"]["pending"]
    await asyncio.gather(organize(service.runtime), organize(service.runtime))
    [round_one] = await history(service, session_id)
    assert round_one["parent_run_id"] is None and round_one["environment_mounts"] == []
    assert len(round_one["memory_mounts"]) == 1
    after = (await service.client.get(f"{service.api}/sessions/{session_id}")).json()
    assert after["preview"] == before["preview"]
    threads = (await service.client.get(f"{service.api}/threads", params={"session_id": session_id})).json()["items"]
    assert [item["id"] for item in threads] == [started["thread"]["id"]]
    scripted_model.say("First organization summary")
    await (await runs_kit.attempt(service))
    request = await scripted_model.request()
    names = {tool["function"]["name"] for tool in request.get("tools", [])}
    assert names and all(name.startswith("memory_file_") for name in names), names
    assert "Business transcript must stay out" not in str(request["messages"])
    await due(service, session_id)
    await organize(service.runtime)
    state = (await service.client.get(f"{service.api}/sessions/{session_id}")).json()["memory_status"]
    assert state["processed_seq"] == 1 and state["active_run_id"] is None and not state["pending"]
    # A later business seal with unchanged memory does not invoke another organizer.
    scripted_model.say("Unchanged")
    await runs_kit.submit(service, started["thread"]["id"], runs_kit.message(agent, "again"))
    await (await runs_kit.attempt(service))
    await scripted_model.request()
    await due(service, session_id)
    await organize(service.runtime)
    assert len(await history(service, session_id)) == 1
    await saved(service, enabled["memory_id"], "second.md")
    scripted_model.say("Changed")
    await runs_kit.submit(service, started["thread"]["id"], runs_kit.message(agent, "changed"))
    await (await runs_kit.attempt(service))
    await scripted_model.request()
    await organize(service.runtime)
    second, first = await history(service, session_id)
    assert second["thread_id"] != first["thread_id"] and second["parent_run_id"] is None
    scripted_model.say("Second organization")
    await (await runs_kit.attempt(service))
    request = await scripted_model.request()
    assert "First organization summary" not in str(request["messages"])


async def test_concurrent_changes_do_not_advance_organization_progress(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    model = await runs_kit.create_model(service, scripted_model)
    agent = await runs_kit.add_agent(service, "Business", model)
    started = await runs_kit.start_thread(service, agent, "hello")
    session_id = started["thread"]["session_id"]
    enabled = await settings(service, session_id, enabled=True, auto_organize=True, model=model)
    await saved(service, enabled["memory_id"])
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    await organize(service.runtime)
    await saved(service, enabled["memory_id"], "concurrent.md")
    scripted_model.say("Organized")
    await (await runs_kit.attempt(service))
    await due(service, session_id)
    await organize(service.runtime)
    state = (await service.client.get(f"{service.api}/sessions/{session_id}")).json()["memory_status"]
    assert state["processed_seq"] == 0 and state["active_run_id"] is None
    assert len(await history(service, session_id)) == 1


async def prepared(service: SimpleNamespace, scripted_model: Any, runs_kit: SimpleNamespace) -> dict[str, Any]:
    await runs_kit.pause_sweeps(service)
    model = await runs_kit.create_model(service, scripted_model)
    agent = await runs_kit.add_agent(service, "Business", model)
    started = await runs_kit.start_thread(service, agent, "hello")
    owner = await settings(service, started["thread"]["session_id"], enabled=True, auto_organize=True, model=model)
    scripted_model.say("Business done")
    await (await runs_kit.attempt(service))
    await scripted_model.request()
    return owner


async def test_empty_disabled_and_deleted_memories_do_not_start_organization(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    owner = await prepared(service, scripted_model, runs_kit)
    await organize(service.runtime)
    assert await history(service, owner["id"]) == []
    await saved(service, owner["memory_id"])
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(SessionMemoryRow).where(SessionMemoryRow.session_id == owner["id"]).values(pending=True)
        )
    await settings(service, owner["id"], enabled=True)
    await organize(service.runtime)
    assert await history(service, owner["id"]) == []
    await settings(service, owner["id"], **owner["memory"])
    memory = await service.client.get(f"{service.api}/memories/{owner['memory_id']}")
    deleted = await service.client.delete(
        f"{service.api}/memories/{owner['memory_id']}", headers={"if-match": memory.headers["etag"]}
    )
    assert deleted.status_code == 204, deleted.text
    await organize(service.runtime)
    assert await history(service, owner["id"]) == []
    current = (await service.client.get(f"{service.api}/sessions/{owner['id']}")).json()
    assert current["memory_id"] is None


async def test_disabling_keeps_an_accepted_round_and_failure_preserves_partial_edits(
    executing, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    service = executing
    await runs_kit.pause_sweeps(service)
    model = await runs_kit.create_model(service, scripted_model)
    agent = await runs_kit.add_agent(service, "Business", model)
    created = await service.client.post(
        f"{service.api}/sessions", json={"memory": {"enabled": True, "auto_organize": True, "model": model}}
    )
    assert created.status_code == 201, created.text
    owner = created.json()
    scripted_model.say("Business done")
    started = await runs_kit.start_thread(service, agent, "hello", session_id=owner["id"])
    await runs_kit.sealed(service, started["run"]["id"])
    await scripted_model.request()
    await saved(service, owner["memory_id"])
    await organize(service.runtime)
    [run] = await history(service, owner["id"])
    await settings(service, owner["id"], enabled=False)
    scripted_model.call(
        "memory_file_create",
        {"memory": "session", "path": "organized.md", "content": "Retained edit."},
        call_id="saved",
    )
    gate = asyncio.Event()
    scripted_model.say("Unfinished", gate=gate)
    await scripted_model.request()
    await scripted_model.request()
    stopped = await service.client.post(f"{service.api}/runs/{run['id']}/interrupt")
    assert stopped.status_code == 200, stopped.text
    # The real worker observes cancellation while the provider response is blocked.
    try:
        cancelled = await runs_kit.sealed(service, run["id"])
        assert cancelled["status"] == "cancelled"
    finally:
        gate.set()
    await due(service, owner["id"])
    await organize(service.runtime)
    state = (await service.client.get(f"{service.api}/sessions/{owner['id']}")).json()["memory_status"]
    assert state["processed_seq"] == 0 and state["failure"]["code"] == "cancelled"
    file = await service.client.get(f"{service.api}/memories/{owner['memory_id']}/files/organized.md")
    assert file.json()["content"] == "Retained edit."
    assert len(await history(service, owner["id"])) == 1


async def test_revocation_blocks_pending_admission(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.tenancy.tables import GrantRow
    from sqlalchemy import delete, select

    owner = await prepared(service, scripted_model, runs_kit)
    await saved(service, owner["memory_id"])
    async with transaction(service.runtime.storage) as session:
        await session.execute(delete(GrantRow).where(GrantRow.principal_id == service.tenant.principal_id))
    await organize(service.runtime)
    async with transaction(service.runtime.storage) as session:
        state = await session.get_one(SessionMemoryRow, owner["id"])
        assert state.active_run_id is None and state.pending and state.failure["code"] == "forbidden"
        assert state.due_at is not None
        assert (
            await session.scalar(select(GrantRow.id).where(GrantRow.principal_id == service.tenant.principal_id))
            is None
        )
