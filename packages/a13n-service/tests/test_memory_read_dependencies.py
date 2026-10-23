"""Read-dependent writes through PostgreSQL, scoped views, and ordinary/organizer Runs."""

import asyncio

import pytest
from a13n_harness.providers.memory import MemoryStoreError, Origin
from a13n_service.resources.memories.store import file_format
from a13n_service.runs.memories.organization import organize
from a13n_service.runs.memories.view import SpaceFileView

from .test_memories import counters, create_memory, revisions, store
from .test_memory_spaces import create_space, due, history, save, status
from .test_runs_memory import mount, team_memory, tool_result

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("change", ["update", "delete", "move"])
@pytest.mark.parametrize("operation", ["create", "write", "move", "delete"])
async def test_postgres_dependency_conflict_has_no_file_revision_or_counter_effect(service, change, operation):
    memory = await create_memory(service)
    files = store(service, memory)
    source = await files.write("source.md", "v1", expected=None, origin=Origin())
    target = await files.write("target.md", "target", expected=None, origin=Origin())
    if change == "update":
        await files.write("source.md", "v2", expected=source, origin=Origin())
    elif change == "delete":
        await files.delete("source.md", expected=source, origin=Origin())
    else:
        await files.move("source.md", "renamed.md", expected=source, origin=Origin())
    before = await files.list()
    totals = await counters(service, memory)
    revision_count = len(await revisions(service, memory))
    dependencies = {"source.md": source}
    calls = {
        "create": lambda: files.write("derived.md", "stale", expected=None, origin=Origin(), dependencies=dependencies),
        "write": lambda: files.write("target.md", "stale", expected=target, origin=Origin(), dependencies=dependencies),
        "move": lambda: files.move(
            "target.md", "derived.md", expected=target, origin=Origin(), dependencies=dependencies
        ),
        "delete": lambda: files.delete("target.md", expected=target, origin=Origin(), dependencies=dependencies),
    }
    with pytest.raises(MemoryStoreError) as caught:
        await calls[operation]()
    assert caught.value.code == "read_conflict"
    assert await files.list() == before
    assert len(await revisions(service, memory)) == revision_count
    after = await counters(service, memory)
    for field in ("seq", "file_count", "content_bytes", "history_bytes"):
        assert getattr(after, field) == getattr(totals, field)


async def test_postgres_cross_file_write_skew_has_one_winner(service):
    memory = await create_memory(service)
    first, second = store(service, memory), store(service, memory)
    a = await first.write("a.md", "a", expected=None, origin=Origin())
    b = await first.write("b.md", "b", expected=None, origin=Origin())
    dependencies = {"a.md": a, "b.md": b}
    outcomes = await asyncio.gather(
        first.write("a.md", "from b", expected=a, origin=Origin(), dependencies=dependencies),
        second.write("b.md", "from a", expected=b, origin=Origin(), dependencies=dependencies),
        return_exceptions=True,
    )
    assert sum(isinstance(outcome, str) for outcome in outcomes) == 1
    errors = [outcome for outcome in outcomes if isinstance(outcome, MemoryStoreError)]
    assert len(errors) == 1 and errors[0].code == "read_conflict"
    assert len(await revisions(service, memory)) == 3


async def test_scoped_dependencies_allow_unrelated_writes_but_never_hidden_paths(service):
    memory = await create_memory(service)
    files = store(service, memory)
    view = SpaceFileView(files, ("global/", "sessions/mine/"), file_format(service.runtime.settings.memory))
    source = await files.write("global/source.md", "source", expected=None, origin=Origin())
    hidden = await files.write("sessions/other/private.md", "private", expected=None, origin=Origin())
    dependencies = {"global/source.md": source, "global/missing.md": None}
    await view.write("global/derived.md", "derived", expected=None, origin=Origin(), dependencies=dependencies)
    with pytest.raises(MemoryStoreError) as caught:
        await view.write(
            "global/leak.md", "leak", expected=None, origin=Origin(), dependencies={"sessions/other/private.md": hidden}
        )
    assert caught.value.code == "forbidden"
    await files.write("global/missing.md", "new", expected=None, origin=Origin())
    with pytest.raises(MemoryStoreError) as caught:
        await view.write("global/refused.md", "stale", expected=None, origin=Origin(), dependencies=dependencies)
    assert caught.value.code == "read_conflict"


async def test_ordinary_run_reconsiders_after_source_conflict(service, scripted_model, runs_kit):
    memory = await team_memory(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    gate = asyncio.Event()
    scripted_model.call(
        "memory_file_create",
        {"memory": "team", "path": "derived.md", "content": "Use tabs"},
        call_id="stale",
        gate=gate,
    )
    scripted_model.call("memory_file_view", {"memory": "team", "path": "README.md"}, call_id="reread")
    scripted_model.call(
        "memory_file_create", {"memory": "team", "path": "derived.md", "content": "Use spaces"}, call_id="fresh"
    )
    scripted_model.say("Recomputed")
    started = await runs_kit.start_thread(service, agent, "derive guidance", memories=[mount(memory)])
    running = await runs_kit.attempt(service)
    await scripted_model.request()
    files = store(service, memory)
    current = await files.read("README.md")
    await files.write("README.md", "Use spaces", expected=current.version, origin=Origin())
    gate.set()
    await running
    assert "read_conflict" in tool_result(await scripted_model.request(), "stale")
    assert "Use spaces" in tool_result(await scripted_model.request(), "reread")
    assert "read_conflict" not in tool_result(await scripted_model.request(), "fresh")
    assert (await files.read("derived.md")).text == "Use spaces"
    assert (await runs_kit.get_run(service, started["run"]["id"]))["status"] == "completed"


async def test_organizer_rejects_stale_derivation_independently_of_progress(service, scripted_model, runs_kit):
    # This test drives organization explicitly; a concurrent sweep may hold its skip-locked candidate.
    await runs_kit.pause_sweeps(service)
    model = await runs_kit.create_model(service, scripted_model)
    space = await create_space(service, organization={"enabled": True, "model": model})
    await save(service, space)
    await organize(service.runtime)
    [run] = await history(service, space)
    name = run["memory_mounts"][0]["name"]
    gate = asyncio.Event()
    scripted_model.call("memory_file_view", {"memory": name, "path": "global/notes.md"}, call_id="read")
    scripted_model.call(
        "memory_file_create",
        {"memory": name, "path": "global/derived.md", "content": "stale"},
        call_id="derive",
        gate=gate,
    )
    scripted_model.say("Conflict acknowledged")
    running = await runs_kit.attempt(service)
    await scripted_model.request()
    await scripted_model.request()
    files = store(service, {"id": space["memory_id"]})
    current = await files.read("global/notes.md")
    await files.write("global/notes.md", "Changed externally", expected=current.version, origin=Origin())
    gate.set()
    await running
    assert "read_conflict" in tool_result(await scripted_model.request(), "derive")
    assert "global/derived.md" not in {entry.path for entry in await files.list()}
    await due(service, space)
    await organize(service.runtime)
    assert (await status(service, space))["processed_seq"] == 0
    assert (await runs_kit.get_run(service, run["id"]))["status"] == "completed"
