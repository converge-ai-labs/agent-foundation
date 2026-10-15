"""Per-Space timing and saved-mutation thresholds survive polling, pruning and organizer writes."""

import asyncio
from datetime import timedelta

import pytest
from a13n_harness.providers.memory import MemoryStoreError, Origin
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.resources.memories.schemas import OrganizationPolicy
from a13n_service.resources.memories.store import PostgresFileStore
from a13n_service.resources.memories.tables import MemoryFileStoreRow
from a13n_service.runs.memories.organization import organize
from a13n_service.runs.memories.organization_tables import SpaceOrganizationRow
from pydantic import ValidationError

from .test_memory_spaces import create_space, due, history, patch, save, status

pytestmark = pytest.mark.anyio


def test_policy_defaults_and_bounds() -> None:
    assert OrganizationPolicy().model_dump() == {
        "enabled": False,
        "model": None,
        "check_interval_seconds": 10,
        "cooldown_seconds": 300,
        "trigger": {"type": "any_change"},
    }
    for fields in [
        {"check_interval_seconds": 9},
        {"check_interval_seconds": 86401},
        {"cooldown_seconds": -1},
        {"cooldown_seconds": 604801},
        {"trigger": {"type": "change_count", "count": 0}},
        {"trigger": {"type": "change_count", "count": 1000001}},
        {"trigger": {"type": "any_change", "count": 1}},
    ]:
        with pytest.raises(ValidationError):
            OrganizationPolicy.model_validate(fields)


async def test_count_survives_checks_moves_noops_failures_and_history_purge(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    model = await runs_kit.create_model(service, scripted_model)
    space = await create_space(
        service,
        organization={
            "enabled": True,
            "model": model,
            "trigger": {"type": "change_count", "count": 3},
        },
    )
    await save(service, space)
    await organize(service.runtime)
    assert await history(service, space) == []
    raw = PostgresFileStore(service.runtime.storage, space["memory_id"], service.runtime.settings.memory)
    same = await raw.write("global/notes.md", "Useful saved knowledge.", expected="1", origin=Origin())
    assert same == "1"
    with pytest.raises(MemoryStoreError):
        await raw.write("global/notes.md", "Stale", expected="0", origin=Origin())
    moved = await raw.move("global/notes.md", "global/moved.md", expected="1", origin=Origin())
    assert moved == "3"
    purged = await service.client.delete(
        f"{service.api}/memories/{space['memory_id']}/revisions", params={"path": "global/notes.md"}
    )
    assert purged.status_code == 200, purged.text
    await due(service, space)
    await organize(service.runtime)
    assert await history(service, space) == []
    async with short_session(service.runtime.storage) as session:
        store = await session.get_one(MemoryFileStoreRow, space["memory_id"])
        assert store.change_count == 2 and store.pruned_through_seq > 0
    await raw.write("global/moved.md", "Changed", expected=moved, origin=Origin())
    # The threshold is ready, but this Space's previous check still gates discovery.
    await organize(service.runtime)
    assert await history(service, space) == []
    await due(service, space)
    await asyncio.gather(organize(service.runtime), organize(service.runtime))
    assert len(await history(service, space)) == 1


async def test_count_excludes_real_organizer_writes_and_retains_concurrent_mutations(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    model = await runs_kit.create_model(service, scripted_model)
    space = await create_space(
        service,
        organization={
            "enabled": True,
            "model": model,
            "trigger": {"type": "change_count", "count": 2},
            "cooldown_seconds": 600,
        },
    )
    await save(service, space)
    await save(service, space, "global/second.md")
    await organize(service.runtime)
    scripted_model.call(
        "memory_file_create",
        {
            "memory": "space",
            "path": "global/summary.md",
            "content": "Summary",
        },
        call_id="summary",
    )
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    await save(service, space, "global/concurrent.md")
    await due(service, space)
    await organize(service.runtime)
    async with short_session(service.runtime.storage) as session:
        store = await session.get_one(MemoryFileStoreRow, space["memory_id"])
        state = await session.get_one(SpaceOrganizationRow, space["id"])
        assert store.change_count == 3 and state.observed_change_count == 2
        assert state.due_at is not None
        assert 590 < (state.due_at - await now(session)).total_seconds() <= 600
    await due(service, space)
    await organize(service.runtime)
    assert len(await history(service, space)) == 1
    await save(service, space, "global/another.md")
    await due(service, space)
    await organize(service.runtime)
    assert len(await history(service, space)) == 2


async def test_manual_bypasses_interval_count_and_cooldown_and_spaces_are_independent(
    service, scripted_model, runs_kit
):  # type: ignore[no-untyped-def]
    model = await runs_kit.create_model(service, scripted_model)
    space = await create_space(
        service,
        organization={
            "enabled": True,
            "model": model,
            "check_interval_seconds": 3600,
            "cooldown_seconds": 1800,
            "trigger": {"type": "change_count", "count": 10},
        },
    )
    await save(service, space)
    await organize(service.runtime)
    async with transaction(service.runtime.storage) as session:
        state = await session.get_one(SpaceOrganizationRow, space["id"])
        at = await now(session)
        assert state.next_check_at is not None
        assert 3590 < (state.next_check_at - at).total_seconds() <= 3600
        state.due_at = at + timedelta(seconds=1800)
    other = await create_space(service, name="Other", organization={"enabled": True, "model": model})
    await save(service, other)
    await organize(service.runtime)
    assert await history(service, space) == []
    assert len(await history(service, other)) == 1
    response = await service.client.post(f"{service.api}/memory-spaces/{space['id']}/organizations")
    assert response.status_code == 202, response.text
    await organize(service.runtime)
    assert len(await history(service, space)) == 1


async def test_failed_admission_preserves_threshold_for_retry(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    model = await runs_kit.create_model(service, scripted_model)
    space = await create_space(
        service,
        organization={
            "enabled": True,
            "model": model,
            "cooldown_seconds": 90,
            "trigger": {"type": "change_count", "count": 1},
        },
    )
    await save(service, space)
    await patch(service, f"models/{model}", enabled=False)
    await organize(service.runtime)
    assert (await status(service, space))["failure"] is not None
    async with short_session(service.runtime.storage) as session:
        state = await session.get_one(SpaceOrganizationRow, space["id"])
        assert state.observed_change_count == 0 and state.configuration_version == 0
        assert state.due_at is not None
        assert 80 < (state.due_at - await now(session)).total_seconds() <= 90
    await patch(service, f"models/{model}", enabled=True)
    await organize(service.runtime)
    assert await history(service, space) == []
    await due(service, space)
    await organize(service.runtime)
    assert len(await history(service, space)) == 1


async def test_below_threshold_checks_do_not_starve_new_work_or_active_settlement(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    from a13n_service.runs.memories.organization import _candidates

    model = await runs_kit.create_model(service, scripted_model)
    policy = {"enabled": True, "model": model, "trigger": {"type": "change_count", "count": 2}}
    waiting, ready = sorted(
        [
            await create_space(service, name="One", organization=policy),
            await create_space(service, name="Two", organization=policy),
        ],
        key=lambda item: item["id"],
    )
    await save(service, waiting)
    await organize(service.runtime)
    await save(service, ready)
    await save(service, ready, "global/second.md")
    async with transaction(service.runtime.storage) as session:
        state = await session.get_one(SpaceOrganizationRow, waiting["id"])
        state.next_check_at = await now(session) - timedelta(seconds=1)
    async with short_session(service.runtime.storage) as session:
        assert list(await session.scalars(_candidates().limit(1))) == [ready["id"]]
    await organize(service.runtime)
    assert len(await history(service, ready)) == 1
    async with transaction(service.runtime.storage) as session:
        at = await now(session)
        waiting_state = await session.get_one(SpaceOrganizationRow, waiting["id"])
        waiting_state.next_check_at = at - timedelta(seconds=1)
        active_state = await session.get_one(SpaceOrganizationRow, ready["id"])
        active_state.due_at = at - timedelta(seconds=2)
    async with short_session(service.runtime.storage) as session:
        assert list(await session.scalars(_candidates().limit(1))) == [ready["id"]]


async def test_empty_manual_request_preserves_count_and_restore_counts_once(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    model = await runs_kit.create_model(service, scripted_model)
    space = await create_space(
        service,
        organization={
            "enabled": True,
            "model": model,
            "trigger": {"type": "change_count", "count": 3},
        },
    )
    await save(service, space)
    root = f"{service.api}/memories/{space['memory_id']}"
    file = await service.client.get(root + "/files/global/notes.md")
    deleted = await service.client.delete(root + "/files/global/notes.md", headers={"if-match": file.headers["etag"]})
    assert deleted.status_code == 204
    requested = await service.client.post(f"{service.api}/memory-spaces/{space['id']}/organizations")
    assert requested.status_code == 202
    await organize(service.runtime)
    assert await history(service, space) == []
    restored = await service.client.post(root + "/revisions/2/restore")
    assert restored.status_code == 200, restored.text
    async with short_session(service.runtime.storage) as session:
        store = await session.get_one(MemoryFileStoreRow, space["memory_id"])
        state = await session.get_one(SpaceOrganizationRow, space["id"])
        assert store.change_count == 3 and state.observed_change_count == 0
    await organize(service.runtime)
    assert len(await history(service, space)) == 1
