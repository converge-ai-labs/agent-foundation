"""One organization executor for standalone files, Spaces and automatic policies."""

import pytest
from a13n_service.infra.db import short_session
from a13n_service.runs.memories.organization_tables import OrganizationRow
from a13n_service.runs.tables import SessionRow
from sqlalchemy import func, select

from .test_memory_spaces import create_space, due, patch, save
from .test_runs_memory import team_memory, text, tool_names

pytestmark = pytest.mark.anyio


@pytest.fixture
async def service(service, runs_kit):
    await runs_kit.pause_sweeps(service)
    return service


async def test_standalone_organization_freezes_request_and_uses_ordinary_worker(service, scripted_model, runs_kit):
    memory = await team_memory(service)
    model = await runs_kit.create_model(service, scripted_model)
    request = {
        "target": {"kind": "memory", "id": memory["id"]},
        "model": model,
        "instructions": "Consolidate tea facts.",
    }
    response = await service.client.post(f"{service.api}/memory-organizations", json=request)
    assert response.status_code == 202, response.text
    organization = response.json()
    assert organization["request"] == request
    run = await runs_kit.get_run(service, organization["run_id"])
    assert run["memory_mounts"][0]["memory_id"] == memory["id"]
    assert run["memory_mounts"][0]["roots"] == []
    assert "organization" not in run["memory_mounts"][0]
    # The persisted snapshot retains exactly the pre-feature field set, including for organizers.
    from a13n_service.runs.memories.execution import resolve_memories
    from a13n_service.runs.tables import RunRow

    async with short_session(service.runtime.storage) as session:
        stored = await session.get_one(RunRow, organization["run_id"])
        assert set(stored.memory_mounts[0]) == {
            "name",
            "memory_id",
            "access",
            "recall",
            "space_id",
            "roots",
            "file_guide",
            "timezone",
        }
        assert (await resolve_memories(session, stored))[0].organizing
    scripted_model.say("No changes needed.")
    await (await runs_kit.attempt(service))
    seen = await scripted_model.request()
    assert "Consolidate tea facts." in text(seen)
    assert "Indent with tabs." in text(seen)
    assert tool_names(seen) and all(name.startswith("memory_file_") for name in tool_names(seen))
    assert (await runs_kit.get_run(service, organization["run_id"]))["status"] == "completed"
    assert (await service.client.get(f"{service.api}/memory-organizations/{organization['id']}")).json() == organization
    listed = (await service.client.get(f"{service.api}/memory-organizations")).json()["items"]
    assert listed == [organization]
    # The managed Session cannot be used as an ordinary conversation to bypass per-request inputs.
    thread = await service.client.post(
        f"{service.api}/threads",
        json={
            "session_id": organization["session_id"],
            **runs_kit.message({"id": run["agent_id"]}, "Another task"),
        },
        headers=runs_kit.fresh_key(),
    )
    assert thread.status_code == 409, thread.text


async def test_organization_rejects_managed_backing_bypass_and_disabled_space(service, scripted_model, runs_kit):
    model = await runs_kit.create_model(service, scripted_model)
    space = await create_space(service, enabled=False)
    for kind, identifier, status_code, reason in (
        ("memory", space["memory_id"], 409, "managed_memory"),
        ("space", space["id"], 422, "disabled"),
    ):
        response = await service.client.post(
            f"{service.api}/memory-organizations", json={"target": {"kind": kind, "id": identifier}, "model": model}
        )
        assert response.status_code == status_code, response.text
        assert reason in response.text
    assert (await service.client.get(f"{service.api}/memory-organizations")).json()["items"] == []


async def test_manual_space_organization_needs_no_automation_and_accepts_empty_store(service, scripted_model, runs_kit):
    model = await runs_kit.create_model(service, scripted_model)
    space = await create_space(service)
    first = await service.client.post(
        f"{service.api}/memory-organizations",
        json={"target": {"kind": "space", "id": space["id"]}, "model": model, "instructions": "Check the empty store."},
    )
    assert first.status_code == 202, first.text
    second = await service.client.post(
        f"{service.api}/memory-organizations",
        json={"target": {"kind": "space", "id": space["id"]}, "model": model, "instructions": "A distinct task."},
    )
    assert second.status_code == 202, second.text
    a, b = first.json(), second.json()
    assert a["run_id"] != b["run_id"] and a["request"]["instructions"] != b["request"]["instructions"]
    run = await runs_kit.get_run(service, a["run_id"])
    assert run["memory_mounts"][0]["roots"] == ["global/", "sessions/"]
    # Disabling future admission does not rewrite an accepted scope or its request.
    await patch(service, f"memory-spaces/{space['id']}", enabled=False)
    assert (await service.client.get(f"{service.api}/memory-organizations/{a['id']}")).json() == a


async def test_automatic_space_round_uses_the_same_request_core(service, scripted_model, runs_kit):
    from a13n_service.runs.memories.organization import organize

    model = await runs_kit.create_model(service, scripted_model)
    space = await create_space(service, organization={"enabled": True, "model": model, "cooldown_seconds": 0})
    await save(service, space)
    await due(service, space)
    await organize(service.runtime)
    async with short_session(service.runtime.storage) as session:
        [row] = list(await session.scalars(select(OrganizationRow)))
        assert row.request["target"] == {"kind": "space", "id": space["id"]}
        assert row.run_id is not None
        run_id = row.run_id
    run = await runs_kit.get_run(service, run_id)
    assert run["memory_mounts"][0]["space_id"] == space["id"]


async def test_rejected_organization_leaves_no_request_or_session(service, scripted_model, runs_kit):
    memory = await team_memory(service)
    response = await service.client.post(
        f"{service.api}/memory-organizations",
        json={"target": {"kind": "memory", "id": memory["id"]}, "model": "missing"},
    )
    assert response.status_code == 400, response.text
    assert "invalid_argument" in response.text
    assert (await service.client.get(f"{service.api}/memory-organizations")).json()["items"] == []
    async with short_session(service.runtime.storage) as session:
        assert await session.scalar(select(func.count()).select_from(SessionRow)) == 0


async def test_ad_hoc_writes_do_not_trigger_automation_but_external_changes_do(service, scripted_model, runs_kit):
    from a13n_service.runs.memories.organization import organize

    from .test_memory_spaces import history

    model = await runs_kit.create_model(service, scripted_model)
    space = await create_space(service, organization={"enabled": True, "model": model, "cooldown_seconds": 0})
    response = await service.client.post(
        f"{service.api}/memory-organizations",
        json={"target": {"kind": "space", "id": space["id"]}, "model": model},
    )
    assert response.status_code == 202, response.text
    scripted_model.call(
        "memory_file_create", {"memory": "space", "path": "global/summary.md", "content": "Summary"}, call_id="summary"
    )
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    await organize(service.runtime)
    assert await history(service, space) == []
    await save(service, space, "global/external.md")
    await due(service, space)
    await organize(service.runtime)
    assert len(await history(service, space)) == 1


def test_historical_organization_mount_decodes_without_rewriting_snapshot():
    from a13n_service.runs.memories.schemas import FrozenMemoryMount

    historical = {
        "memory_id": "mem_" + "a" * 24,
        "name": "space",
        "access": "write",
        "space_id": "msp_old",
        "roots": ["global/", "sessions/"],
    }
    assert FrozenMemoryMount.model_validate(historical).model_dump(mode="json")["roots"] == historical["roots"]
    assert "organization" not in FrozenMemoryMount.model_validate(historical).model_dump(mode="json")
