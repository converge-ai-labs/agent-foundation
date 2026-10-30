"""Session selection is a one-time default snapshot, independent of Space maintenance."""

import asyncio

import pytest
from a13n_service.infra.db import transaction
from a13n_service.runs.memories.organization import organize
from a13n_service.runs.memories.organization_tables import SpaceOrganizationRow
from a13n_service.runs.tables import SessionRow
from sqlalchemy import update

from .test_memory_spaces import binding, create_space, due, history, patch, save, status

pytestmark = pytest.mark.anyio


async def test_defaults_snapshot_once_under_concurrent_acceptance(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    space = await create_space(service)
    model = await runs_kit.create_model(service, scripted_model)
    defaults = {"enabled": True, "spaces": [binding(space)]}
    agent = await runs_kit.add_agent(service, "Remember", model, memory=defaults)
    owner = (await service.client.post(f"{service.api}/sessions", json={})).json()
    first, second = await asyncio.gather(
        runs_kit.start_thread(service, agent, "one", session_id=owner["id"]),
        runs_kit.start_thread(service, agent, "two", session_id=owner["id"]),
    )
    current = (await service.client.get(f"{service.api}/sessions/{owner['id']}")).json()
    assert current["memory"] == defaults
    assert first["run"]["memory_mounts"] == second["run"]["memory_mounts"]
    other = await runs_kit.add_agent(service, "Forget", model)
    joined = await runs_kit.start_thread(service, other, "same", session_id=owner["id"])
    assert joined["run"]["memory_mounts"] == first["run"]["memory_mounts"]
    await patch(service, f"sessions/{owner['id']}", memory={"enabled": False})
    disabled = await runs_kit.start_thread(service, agent, "override", session_id=owner["id"])
    assert disabled["run"]["memory_mounts"] == []


@pytest.mark.parametrize("agent_enabled,session_enabled", [(True, False), (False, True)])
async def test_explicit_enabled_choice_preserves_inherited_selection(
    service, scripted_model, runs_kit, agent_enabled, session_enabled
) -> None:  # type: ignore[no-untyped-def]
    space = await create_space(service)
    agent = await runs_kit.create_agent(
        service, scripted_model, memory={"enabled": agent_enabled, "spaces": [binding(space)]}
    )
    owner = (await service.client.post(f"{service.api}/sessions", json={"memory": {"enabled": session_enabled}})).json()
    started = await runs_kit.start_thread(service, agent, "choice", session_id=owner["id"])
    assert bool(started["run"]["memory_mounts"]) is session_enabled
    current = (await service.client.get(f"{service.api}/sessions/{owner['id']}")).json()
    assert current["memory"] == {"enabled": session_enabled, "spaces": [binding(space)]}


async def test_new_agent_revision_does_not_reconfigure_settled_sessions(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    space = await create_space(service)
    model = await runs_kit.create_model(service, scripted_model)
    agent = await runs_kit.add_agent(service, "Changing defaults", model)
    first = await runs_kit.start_thread(service, agent, "old")
    session_id = first["thread"]["session_id"]
    changed = await service.client.post(
        f"{service.api}/agents/{agent['id']}/revisions",
        json={"config": {"model": model, "memory": {"enabled": True, "spaces": [binding(space)]}}},
        headers=runs_kit.if_match(agent),
    )
    assert changed.status_code == 201, changed.text
    selected = {**agent, "default_revision_id": changed.json()["id"]}
    new = await runs_kit.start_thread(service, selected, "new", agent_revision_id=changed.json()["id"])
    assert new["run"]["memory_mounts"]
    # Older settled Sessions with empty JSON remain settled too.
    async with transaction(service.runtime.storage) as session:
        await session.execute(update(SessionRow).where(SessionRow.id == session_id).values(memory={}))
    old = await runs_kit.start_thread(
        service, selected, "old", session_id=session_id, agent_revision_id=changed.json()["id"]
    )
    assert old["run"]["memory_mounts"] == []


async def test_runner_can_select_spaces_without_resource_write_permission(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    space = await create_space(service)
    agent = await runs_kit.create_agent(service, scripted_model, memory={"enabled": True, "spaces": [binding(space)]})
    accounts = f"{service.workspace}/service-accounts"
    account = (await service.client.post(accounts, json={"name": "runner"})).json()
    key = (await service.client.post(f"{accounts}/{account['id']}/keys", json={"name": "run"})).json()
    headers = {"authorization": "Bearer " + key["secret"]}
    started = await service.client.post(
        f"{service.api}/threads", json=runs_kit.message(agent, "inherit"), headers={**headers, **runs_kit.fresh_key()}
    )
    assert started.status_code == 201, started.text
    run = started.json()["run"]
    assert run["memory_mounts"]
    owner = await service.client.get(f"{service.api}/sessions/{run['session_id']}", headers=headers)
    selected = await service.client.patch(
        f"{service.api}/sessions/{run['session_id']}",
        json={"memory": {"enabled": False}},
        headers={**headers, "if-match": owner.headers["etag"]},
    )
    assert selected.status_code == 200, selected.text
    denied = await service.client.post(f"{service.api}/memory-spaces", json={"name": "No write"}, headers=headers)
    assert denied.status_code == 403


async def test_refused_acceptance_rolls_back_default_snapshot(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    space = await create_space(service)
    agent = await runs_kit.create_agent(service, scripted_model, memory={"enabled": True, "spaces": [binding(space)]})
    owner = (await service.client.post(f"{service.api}/sessions", json={})).json()
    mounts = []
    for index in range(service.runtime.settings.memory.mounts_per_thread):
        memory = (await service.client.post(f"{service.api}/memories", json={"name": f"Memory {index}"})).json()
        mounts.append({"name": f"m{index}", "memory_id": memory["id"], "access": "read"})
    failed = await runs_kit.start_thread(service, agent, "no room", session_id=owner["id"], memories=mounts)
    assert failed["run"] is None and failed["entry"]["status"] == "failed"
    current = (await service.client.get(f"{service.api}/sessions/{owner['id']}")).json()
    assert not current["memory"]["enabled"] and current["version"] == owner["version"]
    assert (await runs_kit.start_thread(service, agent, "retry", session_id=owner["id"]))["run"]["memory_mounts"]


async def test_settled_inheritance_does_not_lock_session(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.resources.memories.schemas import SessionMemorySettings
    from a13n_service.runs.memories.session_memory import inherit_defaults
    from sqlalchemy import select, text

    agent = await runs_kit.create_agent(service, scripted_model, memory={"enabled": True})
    run = (await runs_kit.start_thread(service, agent, "initialized"))["run"]
    async with transaction(service.runtime.storage) as holder:
        owner = await holder.scalar(select(SessionRow).where(SessionRow.id == run["session_id"]).with_for_update())
        assert owner is not None
        async with transaction(service.runtime.storage) as contender:
            await contender.execute(text("SET LOCAL lock_timeout = '100ms'"))
            await inherit_defaults(contender, owner.id, SessionMemorySettings())
            assert (await contender.get_one(SessionRow, owner.id)).memory == owner.memory


async def test_accepted_organization_keeps_partial_edits_after_disable_and_cancel(
    executing, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    service = executing
    await runs_kit.pause_sweeps(service)
    model = await runs_kit.create_model(service, scripted_model)
    space = await create_space(service, organization={"enabled": True, "model": model})
    await save(service, space)
    await organize(service.runtime)
    [run] = await history(service, space)
    await patch(service, f"memory-spaces/{space['id']}", enabled=False)
    scripted_model.call(
        "memory_file_create",
        {"memory": "space", "path": "global/organized.md", "content": "Retained edit."},
        call_id="saved",
    )
    gate = asyncio.Event()
    scripted_model.say("Unfinished", gate=gate)
    await scripted_model.request()
    await scripted_model.request()
    stopped = await service.client.post(f"{service.api}/runs/{run['id']}/interrupt")
    assert stopped.status_code == 200, stopped.text
    try:
        assert (await runs_kit.sealed(service, run["id"]))["status"] == "cancelled"
    finally:
        gate.set()
    await due(service, space)
    await organize(service.runtime)
    state = await status(service, space)
    assert state["processed_seq"] == 0 and state["failure"]["code"] == "cancelled"
    file = await service.client.get(f"{service.api}/memories/{space['memory_id']}/files/global/organized.md")
    assert file.json()["content"] == "Retained edit."
    assert len(await history(service, space)) == 1


async def test_revocation_blocks_pending_admission(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.tenancy.tables import GrantRow
    from sqlalchemy import delete

    await runs_kit.pause_sweeps(service)
    model = await runs_kit.create_model(service, scripted_model)
    space = await create_space(service, organization={"enabled": True, "model": model})
    await save(service, space)
    async with transaction(service.runtime.storage) as session:
        await session.execute(delete(GrantRow).where(GrantRow.principal_id == service.tenant.principal_id))
    await organize(service.runtime)
    async with transaction(service.runtime.storage) as session:
        state = await session.get_one(SpaceOrganizationRow, space["id"])
        assert state.active_run_id is None and state.failure["code"] == "forbidden"
        assert state.due_at is not None


async def test_organization_session_rejects_business_input_and_selection(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    model = await runs_kit.create_model(service, scripted_model)
    agent = await runs_kit.add_agent(service, "Business", model)
    space = await create_space(service, organization={"enabled": True, "model": model})
    await save(service, space)
    await organize(service.runtime)
    [run] = await history(service, space)
    response = await service.client.post(
        f"{service.api}/threads",
        json=runs_kit.message(agent, "business", session_id=run["session_id"]),
        headers=runs_kit.fresh_key(),
    )
    assert response.status_code == 409, response.text
    owner = await service.client.get(f"{service.api}/sessions/{run['session_id']}")
    response = await service.client.patch(
        f"{service.api}/sessions/{run['session_id']}",
        json={"memory": {"enabled": True}},
        headers={"if-match": owner.headers["etag"]},
    )
    assert response.status_code == 409, response.text
