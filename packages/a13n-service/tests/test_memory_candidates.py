"""Candidate output preserves live files until an explicitly reviewed atomic apply."""

import asyncio

import pytest
from a13n_service.infra.db import short_session
from a13n_service.resources.memories.tables import MemoryFileRow, MemoryFileStoreRow
from sqlalchemy import select

from .test_runs_memory import team_memory, write_file

pytestmark = pytest.mark.anyio


@pytest.fixture
async def service(service, runs_kit):
    await runs_kit.pause_sweeps(service)
    return service


async def candidate(service, scripted_model, runs_kit, *, target=None):
    memory = await team_memory(service) if target is None else target
    model = await runs_kit.create_model(service, scripted_model)
    response = await service.client.post(
        f"{service.api}/memory-organizations",
        json={"target": {"kind": "memory", "id": memory["id"]}, "model": model, "output": "candidate"},
    )
    assert response.status_code == 202, response.text
    return memory, response.json()


def endpoint(service, organization):
    return f"{service.api}/memory-organizations/{organization['id']}/candidate"


async def preview(service, organization):
    response = await service.client.get(endpoint(service, organization))
    assert response.status_code == 200, response.text
    return response.json()


def reviewed(value, mode="checked"):
    return {"mode": mode, "candidate_seq": value["candidate_seq"], "target_seq": value["target_seq"]}


async def complete(service, scripted_model, runs_kit):
    scripted_model.say("Ready for review")
    await (await runs_kit.attempt(service))


async def test_candidate_worker_isolated_then_apply_preserves_unchanged_identity(service, scripted_model, runs_kit):
    memory, organization = await candidate(service, scripted_model, runs_kit)
    destination = organization["candidate_memory_id"]
    run = await runs_kit.get_run(service, organization["run_id"])
    assert run["memory_mounts"][0]["memory_id"] == destination != memory["id"]
    assert (await preview(service, organization))["status"] == "generating"
    async with short_session(service.runtime.storage) as session:
        original = (
            await session.scalars(
                select(MemoryFileRow).where(MemoryFileRow.memory_id == memory["id"]).order_by(MemoryFileRow.path)
            )
        ).all()
        identities = {row.path: (row.id, row.version) for row in original}
    scripted_model.call(
        "memory_file_create", {"memory": "memory", "path": "summary.md", "content": "Tea summary"}, call_id="save"
    )
    await complete(service, scripted_model, runs_kit)
    assert (await service.client.get(f"{service.api}/memories/{memory['id']}/files/summary.md")).status_code == 404
    value = await preview(service, organization)
    assert value["status"] == "ready"
    assert value["changes"] == [{"path": "summary.md", "operation": "create"}]
    diff = (await service.client.get(f"{endpoint(service, organization)}/diff", params={"path": "summary.md"})).json()
    assert "+Tea summary" in diff["diff"] and not diff["truncated"]
    request = reviewed(value)
    applied = await service.client.post(f"{endpoint(service, organization)}/apply", json=request)
    assert applied.status_code == 200, applied.text
    receipt = applied.json()["application"]
    assert applied.json()["status"] == "applied" and receipt["result_seq"] == value["target_seq"] + 1
    assert (await service.client.post(f"{endpoint(service, organization)}/apply", json=request)).json()[
        "application"
    ] == receipt
    async with short_session(service.runtime.storage) as session:
        current = (await session.scalars(select(MemoryFileRow).where(MemoryFileRow.memory_id == memory["id"]))).all()
        assert {row.path: (row.id, row.version) for row in current if row.path in identities} == identities
        store = await session.get_one(MemoryFileStoreRow, memory["id"])
        assert store.file_count == 3 and store.content_bytes == sum(row.size for row in current)
        assert store.change_count == 2  # Publication does not retrigger organization.
    assert (await service.client.post(f"{endpoint(service, organization)}/discard")).status_code == 409


async def test_candidate_diff_uses_lf_lines_and_preserves_final_newlines(service, scripted_model, runs_kit):
    memory = await team_memory(service)
    cases = [
        ("replace.md", "old", "new", "-old\n\\ No newline at end of file\n+new\n\\ No newline at end of file\n"),
        ("add-newline.md", "same", "same\n", "-same\n\\ No newline at end of file\n+same\n"),
        ("remove-newline.md", "same\n", "same", "-same\n+same\n\\ No newline at end of file\n"),
        ("form-feed.md", "a\fb\n", "a\fc\n", "-a\fb\n+a\fc\n"),
        ("unicode-separator.md", "a\u2028b\n", "a\u2028c\n", "-a\u2028b\n+a\u2028c\n"),
        ("carriage-return.md", "a\rb\n", "a\rc\n", "-a\rb\n+a\rc\n"),
    ]
    for path, before, _, _ in cases:
        await write_file(service, memory, path, before)
    _, organization = await candidate(service, scripted_model, runs_kit, target=memory)
    for path, _, after, expected in cases:
        await edit_file(service, organization["candidate_memory_id"], path, after)
        response = await service.client.get(f"{endpoint(service, organization)}/diff", params={"path": path})
        assert response.status_code == 200, response.text
        value = response.json()
        assert value["diff"] == f"--- baseline/{path}\n+++ candidate/{path}\n@@ -1 +1 @@\n{expected}"
        assert not value["truncated"]


async def test_checked_conflict_and_explicit_replace_all_require_current_review(service, scripted_model, runs_kit):
    memory, organization = await candidate(service, scripted_model, runs_kit)
    await complete(service, scripted_model, runs_kit)
    old = reviewed(await preview(service, organization))
    await write_file(service, memory, "concurrent.md", "Keep unless explicitly replaced")
    response = await service.client.post(f"{endpoint(service, organization)}/apply", json=old)
    assert response.status_code == 409 and "review_changed" in response.text
    value = await preview(service, organization)
    response = await service.client.post(f"{endpoint(service, organization)}/apply", json=reviewed(value))
    assert response.status_code == 409 and "baseline_changed" in response.text
    # Editing the ordinary candidate Memory invalidates the reviewed candidate sequence too.
    await write_file(service, {"id": organization["candidate_memory_id"]}, "new.md", "Reviewed later")
    response = await service.client.post(
        f"{endpoint(service, organization)}/apply", json=reviewed(value, "replace_all")
    )
    assert response.status_code == 409 and "review_changed" in response.text
    value = await preview(service, organization)
    response = await service.client.post(
        f"{endpoint(service, organization)}/apply", json=reviewed(value, "replace_all")
    )
    assert response.status_code == 200, response.text
    assert (await service.client.get(f"{service.api}/memories/{memory['id']}/files/concurrent.md")).status_code == 404
    assert (await service.client.get(f"{service.api}/memories/{memory['id']}/files/new.md")).json()[
        "content"
    ] == "Reviewed later"


async def test_unsealed_and_discarded_candidates_cannot_apply(service, scripted_model, runs_kit):
    _, organization = await candidate(service, scripted_model, runs_kit)
    url = endpoint(service, organization)
    response = await service.client.post(f"{url}/apply", json=reviewed(await preview(service, organization)))
    assert response.status_code == 409 and "candidate_not_ready" in response.text
    assert (await service.client.post(f"{url}/discard")).status_code == 409
    await complete(service, scripted_model, runs_kit)
    value = await preview(service, organization)
    responses = await asyncio.gather(
        service.client.post(f"{url}/discard"), service.client.post(f"{url}/apply", json=reviewed(value))
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    final = await preview(service, organization)
    assert final["status"] in {"applied", "discarded"}


async def test_deleted_candidate_reports_unavailable_without_false_deletions(service, scripted_model, runs_kit):
    _, organization = await candidate(service, scripted_model, runs_kit)
    await complete(service, scripted_model, runs_kit)
    url = f"{service.api}/memories/{organization['candidate_memory_id']}"
    memory = await service.client.get(url)
    response = await service.client.delete(url, headers={"If-Match": memory.headers["etag"]})
    assert response.status_code == 204, response.text
    value = await preview(service, organization)
    assert value["status"] == "unavailable" and value["changes"] == []
    response = await service.client.get(f"{endpoint(service, organization)}/diff", params={"path": "README.md"})
    assert response.status_code == 409 and "candidate_unavailable" in response.text


async def edit_file(service, memory_id, path, content=None):
    url = f"{service.api}/memories/{memory_id}/files/{path}"
    before = await service.client.get(url)
    headers = {"If-Match": before.headers["etag"]}
    response = (
        await service.client.delete(url, headers=headers)
        if content is None
        else await service.client.put(url, headers=headers, json={"content": content})
    )
    assert response.status_code == (204 if content is None else 200), response.text


async def test_apply_updates_deletes_and_creates_in_one_tree_and_replays_concurrently(
    service, scripted_model, runs_kit
):
    memory, organization = await candidate(service, scripted_model, runs_kit)
    candidate_id = organization["candidate_memory_id"]
    await complete(service, scripted_model, runs_kit)
    await edit_file(service, candidate_id, "README.md", "New navigation")
    await edit_file(service, candidate_id, "notes/tea.md")
    await write_file(service, {"id": candidate_id}, "new.md", "New knowledge")
    value = await preview(service, organization)
    assert value["changes"] == [
        {"path": "README.md", "operation": "update"},
        {"path": "new.md", "operation": "create"},
        {"path": "notes/tea.md", "operation": "delete"},
    ]
    responses = await asyncio.gather(
        *(service.client.post(f"{endpoint(service, organization)}/apply", json=reviewed(value)) for _ in range(2))
    )
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json()["application"] == responses[1].json()["application"]
    assert responses[0].json()["application"]["result_seq"] == value["target_seq"] + 3
    assert (await service.client.get(f"{service.api}/memories/{memory['id']}/files/README.md")).json()[
        "content"
    ] == "New navigation"
    assert (await service.client.get(f"{service.api}/memories/{memory['id']}/files/notes/tea.md")).status_code == 404
    async with short_session(service.runtime.storage) as session:
        state = await session.get_one(MemoryFileStoreRow, memory["id"])
        assert state.file_count == 2 and state.content_bytes == len("New navigationNew knowledge")


async def test_space_candidate_keeps_roots_and_requires_current_enabled_space(service, scripted_model, runs_kit):
    from .test_memory_spaces import create_space, patch, save

    space = await create_space(service)
    await save(service, space)
    model = await runs_kit.create_model(service, scripted_model)
    response = await service.client.post(
        f"{service.api}/memory-organizations",
        json={"target": {"kind": "space", "id": space["id"]}, "model": model, "output": "candidate"},
    )
    assert response.status_code == 202, response.text
    organization = response.json()
    mount = (await runs_kit.get_run(service, organization["run_id"]))["memory_mounts"][0]
    assert mount["roots"] == ["global/", "sessions/"] and mount["memory_id"] == organization["candidate_memory_id"]
    scripted_model.call(
        "memory_file_create",
        {"memory": mount["name"], "path": "global/summary.md", "content": "Shared knowledge"},
        call_id="save",
    )
    await complete(service, scripted_model, runs_kit)
    assert (
        await service.client.get(f"{service.api}/memories/{space['memory_id']}/files/global/summary.md")
    ).status_code == 404
    value = await preview(service, organization)
    await patch(service, f"memory-spaces/{space['id']}", enabled=False)
    url = f"{endpoint(service, organization)}/apply"
    response = await service.client.post(url, json=reviewed(value))
    assert response.status_code == 409 and "target_unavailable" in response.text
    await patch(service, f"memory-spaces/{space['id']}", enabled=True)
    await write_file(service, {"id": organization["candidate_memory_id"]}, "outside.md", "Not a Space root")
    response = await service.client.post(url, json=reviewed(await preview(service, organization)))
    assert response.status_code == 409 and "outside_space_roots" in response.text
    await edit_file(service, organization["candidate_memory_id"], "outside.md")
    response = await service.client.post(url, json=reviewed(await preview(service, organization)))
    assert response.status_code == 200, response.text
    assert (await service.client.get(f"{service.api}/memories/{space['memory_id']}/files/global/summary.md")).json()[
        "content"
    ] == "Shared knowledge"


async def test_replace_files_validates_final_tree_and_rolls_back_failed_publication(service):
    from a13n_harness.providers.memory import MemoryStoreError, Origin
    from a13n_service.infra.db import transaction
    from a13n_service.resources.memories import store

    memory = await team_memory(service)
    settings = service.runtime.settings.memory.model_copy(update={"max_total_bytes": 64})
    async with transaction(service.runtime.storage) as session:
        seq = await store.replace_files(
            session, settings, memory["id"], {"z.md": "x" * 64}, expected_seq=2, origin=Origin()
        )
        assert seq == 5
    # Creating a.md before deleting z.md would exceed the budget; only the final tree counts.
    async with transaction(service.runtime.storage) as session:
        assert (
            await store.replace_files(
                session, settings, memory["id"], {"a.md": "y" * 64}, expected_seq=seq, origin=Origin()
            )
            == 7
        )
    with pytest.raises(MemoryStoreError, match="byte limit"):
        async with transaction(service.runtime.storage) as session:
            await store.replace_files(
                session, settings, memory["id"], {"too.md": "z" * 65}, expected_seq=7, origin=Origin()
            )
    async with short_session(service.runtime.storage) as session:
        state = await session.get_one(MemoryFileStoreRow, memory["id"])
        assert state.seq == 7 and state.content_bytes == 64 and state.file_count == 1
        assert (await store.read_file(session, settings, memory["id"], "a.md")).content == "y" * 64


async def test_cancelled_generation_can_be_discarded_but_never_applied(service, scripted_model, runs_kit):
    _, organization = await candidate(service, scripted_model, runs_kit)
    run = await runs_kit.get_run(service, organization["run_id"])
    response = await service.client.post(
        f"{service.api}/runs/{run['id']}/interrupt", headers={"If-Match": f'"{run["id"]}:{run["version"]}"'}
    )
    assert response.status_code == 200, response.text
    value = await preview(service, organization)
    assert value["status"] == "failed"
    response = await service.client.post(f"{endpoint(service, organization)}/apply", json=reviewed(value))
    assert response.status_code == 409 and "candidate_not_ready" in response.text
    for _ in range(2):
        response = await service.client.post(f"{endpoint(service, organization)}/discard")
        assert response.status_code == 200 and response.json()["status"] == "discarded"
