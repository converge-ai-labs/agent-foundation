"""Shared Spaces, new-Run views and Space-owned organization through ordinary workers."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from a13n_harness.providers.memory import Changes, MemoryStoreError, Origin
from a13n_service.infra.db import transaction
from a13n_service.resources.memories.store import PostgresFileStore, file_format
from a13n_service.runs.memories.organization import organize
from a13n_service.runs.memories.organization_tables import SpaceOrganizationRow
from a13n_service.runs.memories.view import SpaceFileView
from sqlalchemy import update

pytestmark = pytest.mark.anyio


async def create_space(service: SimpleNamespace, **fields: Any) -> dict[str, Any]:
    response = await service.client.post(f"{service.api}/memory-spaces", json={"name": "Knowledge", **fields})
    assert response.status_code == 201, response.text
    return response.json()


def binding(space: dict[str, Any], name: str = "notes", access: str = "write") -> dict[str, Any]:
    return {"name": name, "space_id": space["id"], "access": access}


async def patch(service: SimpleNamespace, path: str, **fields: Any) -> dict[str, Any]:
    url = f"{service.api}/{path}"
    before = await service.client.get(url)
    response = await service.client.patch(url, json=fields, headers={"if-match": before.headers["etag"]})
    assert response.status_code == 200, response.text
    return response.json()


async def save(service: SimpleNamespace, space: dict[str, Any], path: str = "global/notes.md") -> None:
    response = await service.client.post(
        f"{service.api}/memories/{space['memory_id']}/files", json={"path": path, "content": "Useful saved knowledge."}
    )
    assert response.status_code == 201, response.text


async def due(service: SimpleNamespace, space: dict[str, Any]) -> None:
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(SpaceOrganizationRow)
            .where(
                SpaceOrganizationRow.space_id == space["id"],
            )
            .values(due_at=None, next_check_at=None)
        )


async def history(service: SimpleNamespace, space: dict[str, Any]) -> list[dict[str, Any]]:
    response = await service.client.get(f"{service.api}/memory-spaces/{space['id']}/organizations")
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def status(service: SimpleNamespace, space: dict[str, Any]) -> dict[str, Any]:
    response = await service.client.get(f"{service.api}/memory-spaces/{space['id']}/organization")
    assert response.status_code == 200, response.text
    return response.json()


async def test_spaces_share_storage_but_freeze_session_views(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    space = await create_space(service)
    agent = await runs_kit.create_agent(service, scripted_model, memory={"enabled": True, "spaces": [binding(space)]})
    first = await runs_kit.start_thread(service, agent, "first")
    second = await runs_kit.start_thread(service, agent, "second")
    a, b = first["run"]["memory_mounts"][0], second["run"]["memory_mounts"][0]
    assert a["memory_id"] == b["memory_id"] == space["memory_id"]
    assert a["roots"] == ["global/", f"sessions/{first['thread']['session_id']}/"]
    assert a["roots"] != b["roots"]
    disabled = await patch(service, f"memory-spaces/{space['id']}", enabled=False)
    assert disabled["memory_id"] == space["memory_id"]
    assert (await runs_kit.get_run(service, first["run"]["id"]))["memory_mounts"] == [a]
    later = await runs_kit.start_thread(service, agent, "later", session_id=first["thread"]["session_id"])
    assert later["run"]["memory_mounts"] == []
    await patch(service, f"memory-spaces/{space['id']}", enabled=True)
    restored = await runs_kit.start_thread(service, agent, "restored", session_id=first["thread"]["session_id"])
    assert restored["run"]["memory_mounts"] == [a]


async def test_explicit_selection_replaces_defaults_and_empty_creates_nothing(
    service, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    shared, project = await create_space(service), await create_space(service, name="Project")
    model = await runs_kit.create_model(service, scripted_model)
    agent = await runs_kit.add_agent(
        service, "Default", model, memory={"enabled": True, "spaces": [binding(shared, "shared")]}
    )
    explicit = (
        await service.client.post(f"{service.api}/sessions", json={"memory": {"spaces": [binding(project)]}})
    ).json()
    started = await runs_kit.start_thread(service, agent, "replace", session_id=explicit["id"])
    assert [item["space_id"] for item in started["run"]["memory_mounts"]] == [project["id"]]
    empty = (await service.client.post(f"{service.api}/sessions", json={"memory": {"spaces": []}})).json()
    started = await runs_kit.start_thread(service, agent, "empty", session_id=empty["id"])
    assert started["run"]["memory_mounts"] == []
    plain = await runs_kit.add_agent(service, "Plain", model, memory={"enabled": True})
    assert (await runs_kit.start_thread(service, plain, "no implicit Space"))["run"]["memory_mounts"] == []
    spaces = (await service.client.get(f"{service.api}/memory-spaces")).json()["items"]
    assert len(spaces) == 2
    # A different Agent cannot replace a settled Session's selection.
    assert (await runs_kit.start_thread(service, plain, "unchanged", session_id=explicit["id"]))["run"]["memory_mounts"]


async def test_space_view_hides_siblings_and_allows_global_cas_edits(service) -> None:  # type: ignore[no-untyped-def]
    space = await create_space(service)
    await save(service, space)
    await save(service, space, "sessions/other/private.md")
    raw = PostgresFileStore(service.runtime.storage, space["memory_id"], service.runtime.settings.memory)
    scoped = SpaceFileView(raw, ("global/", "sessions/own/"), file_format(service.runtime.settings.memory))
    checkpoint = await scoped.changes(None)
    assert [item.path for item in await scoped.list()] == ["global/notes.md"]
    version = await scoped.write("global/summary.md", "Shared", expected=None, origin=Origin())
    await scoped.write("sessions/own/note.md", "Private", expected=None, origin=Origin())
    with pytest.raises(MemoryStoreError) as stale:
        await scoped.write("global/summary.md", "Stale", expected="0", origin=Origin())
    assert stale.value.code == "version_mismatch" and stale.value.current.text == "Shared"
    for path in ["sessions/other/private.md", "global/../sessions/other/private.md"]:
        with pytest.raises(MemoryStoreError):
            await scoped.read(path)
        with pytest.raises(MemoryStoreError):
            await scoped.move("global/summary.md", path, expected=version, origin=Origin())
    await raw.write("sessions/other/new.md", "Hidden", expected=None, origin=Origin())
    changes = await scoped.changes(checkpoint.cursor)
    assert isinstance(changes, Changes)
    assert set(changes.paths) == {"global/summary.md", "sessions/own/note.md"}
    with pytest.raises(MemoryStoreError):
        await scoped.purge()


async def test_managed_backing_refuses_raw_mount_and_configuration_but_keeps_files(
    service, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    space = await create_space(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    response = await service.client.post(
        f"{service.api}/threads",
        json=runs_kit.message(
            agent,
            "bypass",
            memories=[
                {"name": "raw", "memory_id": space["memory_id"], "access": "write"},
            ],
        ),
        headers=runs_kit.fresh_key(),
    )
    assert response.status_code == 409, response.text
    memory = await service.client.get(f"{service.api}/memories/{space['memory_id']}")
    response = await service.client.patch(
        f"{service.api}/memories/{space['memory_id']}",
        json={"name": "Bypass"},
        headers={"if-match": memory.headers["etag"]},
    )
    assert response.status_code == 409
    await patch(service, f"memory-spaces/{space['id']}", enabled=False)
    await save(service, space)
    assert not (await service.client.get(f"{service.api}/memories")).json()["items"]


async def test_disabled_space_does_not_hide_other_mounts(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    a, b = await create_space(service), await create_space(service, name="Other")
    await patch(service, f"memory-spaces/{a['id']}", enabled=False)
    agent = await runs_kit.create_agent(
        service,
        scripted_model,
        memory={"enabled": True, "spaces": [binding(a, "personal"), binding(b, "project", "read")]},
    )
    started = await runs_kit.start_thread(service, agent, "remaining")
    [mount] = started["run"]["memory_mounts"]
    assert mount["space_id"] == b["id"] and mount["access"] == "read"
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    request = await scripted_model.request()
    names = {item["function"]["name"] for item in request["tools"]}
    assert "memory_file_view" in names and "memory_file_create" not in names
    await patch(service, f"memory-spaces/{b['id']}", enabled=False)
    await runs_kit.start_thread(service, agent, "none")
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    request = await scripted_model.request()
    assert not any(item["function"]["name"].startswith("memory_") for item in request.get("tools", []))


async def test_api_changes_organize_in_one_dedicated_session_with_fresh_threads(
    service, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    model = await runs_kit.create_model(service, scripted_model)
    space = await create_space(service, organization={"enabled": True, "model": model})
    await save(service, space)
    await asyncio.gather(organize(service.runtime), organize(service.runtime))
    [first] = await history(service, space)
    assert first["parent_run_id"] is None and first["environment_mounts"] == []
    assert first["options"]["max_usage"]["requests"] == 200
    assert first["memory_mounts"][0]["roots"] == ["global/", "sessions/"]
    scripted_model.say("Organized first round")
    await (await runs_kit.attempt(service))
    await scripted_model.request()
    await due(service, space)
    await organize(service.runtime)
    assert (await status(service, space))["processed_seq"] == 1
    assert not (await service.client.get(f"{service.api}/sessions")).json()["items"]
    await save(service, space, "sessions/business/note.md")
    await due(service, space)
    await organize(service.runtime)
    second, previous = await history(service, space)
    assert previous["id"] == first["id"]
    assert second["session_id"] == first["session_id"] and second["thread_id"] != first["thread_id"]
    scripted_model.say("Organized second round")
    await (await runs_kit.attempt(service))
    request = await scripted_model.request()
    assert "Organized first round" not in str(request["messages"])


async def test_concurrent_edits_requeue_without_false_progress(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    model = await runs_kit.create_model(service, scripted_model)
    space = await create_space(service, organization={"enabled": True, "model": model})
    await save(service, space)
    await organize(service.runtime)
    await save(service, space, "global/concurrent.md")
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    await scripted_model.request()
    await due(service, space)
    await organize(service.runtime)
    state = await status(service, space)
    assert state["processed_seq"] == 0 and not state["pending"]
    await due(service, space)
    await organize(service.runtime)
    assert len(await history(service, space)) == 2


async def test_disabling_automatic_organization_stops_followup_but_allows_manual(
    service, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    model = await runs_kit.create_model(service, scripted_model)
    space = await create_space(service, organization={"enabled": True, "model": model})
    await save(service, space)
    await organize(service.runtime)
    await save(service, space, "global/concurrent.md")
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    await due(service, space)
    await organize(service.runtime)
    await patch(service, f"memory-spaces/{space['id']}", organization={"enabled": False, "model": model})
    await due(service, space)
    await organize(service.runtime)
    assert len(await history(service, space)) == 1
    requested = await service.client.post(f"{service.api}/memory-spaces/{space['id']}/organizations", json={})
    assert requested.status_code == 202, requested.text
    await organize(service.runtime)
    assert len(await history(service, space)) == 2


async def test_configuration_trigger_retries_after_model_is_reenabled(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    model = await runs_kit.create_model(service, scripted_model)
    space = await create_space(service, organization={"enabled": True, "model": model})
    await save(service, space)
    await organize(service.runtime)
    [first] = await history(service, space)
    stopped = await service.client.post(f"{service.api}/runs/{first['id']}/interrupt")
    assert stopped.status_code == 200, stopped.text
    await due(service, space)
    await organize(service.runtime)
    assert (await status(service, space))["processed_seq"] == 0
    await patch(service, f"memory-spaces/{space['id']}", guide="Organize project knowledge.")
    await patch(service, f"models/{model}", enabled=False)
    await due(service, space)
    await organize(service.runtime)
    failed = await status(service, space)
    assert failed["failure"] is not None and failed["active_run_id"] is None
    assert len(await history(service, space)) == 1
    await patch(service, f"models/{model}", enabled=True)
    # The recorded cooldown remains in effect after the model recovers.
    await organize(service.runtime)
    assert len(await history(service, space)) == 1
    await due(service, space)
    await organize(service.runtime)
    assert len(await history(service, space)) == 2
    assert (await status(service, space))["failure"] is None


async def test_disable_stops_organization_and_reenable_resumes_saved_changes(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    model = await runs_kit.create_model(service, scripted_model)
    space = await create_space(service, organization={"enabled": True, "model": model})
    await save(service, space)
    await patch(service, f"memory-spaces/{space['id']}", enabled=False)
    await organize(service.runtime)
    assert await history(service, space) == []
    await patch(service, f"memory-spaces/{space['id']}", enabled=True)
    await organize(service.runtime)
    assert len(await history(service, space)) == 1


async def test_space_etags_associations_and_coordinated_delete(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    space = await create_space(service)
    path = f"{service.api}/memory-spaces/{space['id']}"
    original = await service.client.get(path)
    changed = await patch(service, f"memory-spaces/{space['id']}", guide="Keep durable decisions.")
    assert changed["version"] > space["version"]
    stale = await service.client.patch(path, json={"name": "Stale"}, headers={"if-match": original.headers["etag"]})
    assert stale.status_code == 412
    owner = (await service.client.post(f"{service.api}/sessions", json={"memory": {"spaces": [binding(space)]}})).json()
    associated = await service.client.get(path + "/sessions")
    assert [item["id"] for item in associated.json()["items"]] == [owner["id"]]
    current = await service.client.get(path)
    deleted = await service.client.delete(path, headers={"if-match": current.headers["etag"]})
    assert deleted.status_code == 409 and deleted.json()["error"]["details"]["reason"] == "space_bound"
    await patch(service, f"sessions/{owner['id']}", memory={"enabled": True})
    agent = await runs_kit.create_agent(service, scripted_model)
    await runs_kit.start_thread(service, agent, "using", session_id=owner["id"])
    await patch(service, f"sessions/{owner['id']}", memory={"spaces": []})
    deleted = await service.client.delete(path, headers={"if-match": current.headers["etag"]})
    assert deleted.status_code == 409
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    deleted = await service.client.delete(path, headers={"if-match": current.headers["etag"]})
    assert deleted.status_code == 204, deleted.text
    assert (await service.client.get(f"{service.api}/memories/{space['memory_id']}")).status_code == 404


async def test_pruned_organization_history_does_not_self_schedule(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.resources.memories.tables import MemoryFileStoreRow

    model = await runs_kit.create_model(service, scripted_model)
    space = await create_space(service, organization={"enabled": True, "model": model})
    await save(service, space)
    await organize(service.runtime)
    scripted_model.call(
        "memory_file_create", {"memory": "space", "path": "global/summary.md", "content": "Summary"}, call_id="save"
    )
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(MemoryFileStoreRow)
            .where(MemoryFileStoreRow.memory_id == space["memory_id"])
            .values(pruned_through_seq=2)
        )
    await due(service, space)
    await organize(service.runtime)
    state = await status(service, space)
    assert state["history_gap"] and state["processed_seq"] == 0 and not state["pending"]
    await due(service, space)
    await organize(service.runtime)
    assert len(await history(service, space)) == 1
    requested = await service.client.post(f"{service.api}/memory-spaces/{space['id']}/organizations", json={})
    assert requested.status_code == 202
    await organize(service.runtime)
    assert len(await history(service, space)) == 2


@pytest.mark.parametrize("disable", ["space", "session"])
async def test_child_admission_observes_current_disable_after_parent_snapshot(
    service, scripted_model, runs_kit, disable
) -> None:  # type: ignore[no-untyped-def]
    from .test_runs_children import _child_run

    space = await create_space(service)
    agent = await runs_kit.delegating(service, scripted_model, "async")
    owner = (
        await service.client.post(
            f"{service.api}/sessions", json={"memory": {"enabled": True, "spaces": [binding(space)]}}
        )
    ).json()
    parent = await runs_kit.start_thread(service, agent, "delegate", session_id=owner["id"])
    if disable == "space":
        await patch(service, f"memory-spaces/{space['id']}", enabled=False)
    else:
        await patch(service, f"sessions/{owner['id']}", memory={"enabled": False})
    scripted_model.call(
        "delegate", {"subagent_name": "helper", "prompt": "compute"}, call_id="delegate", to="Role: coordinator"
    )
    scripted_model.say("Delegated", to="Role: coordinator")
    await (await runs_kit.attempt(service))
    request = await scripted_model.request()
    assert "memory_file_view" in {item["function"]["name"] for item in request["tools"]}
    child = await _child_run(service, parent["run"]["id"])
    assert child.memory_mounts == []
