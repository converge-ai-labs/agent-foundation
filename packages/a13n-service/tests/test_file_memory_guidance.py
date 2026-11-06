"""Guidance ownership, override semantics, clock context and acceptance freezing."""

import pytest
from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.memories.guidance import FileMemoryGuidance, resolve_guidance, validate_guidance
from a13n_service.runs.memories.execution import PlannedMemory, _guide
from a13n_service.runs.memories.schemas import FrozenMemoryMount
from a13n_service.runs.tables import RunRow
from a13n_service.settings import MemorySettings

from .test_memory_spaces import binding, create_space, patch

pytestmark = pytest.mark.anyio


def test_system_override_and_business_guidance_are_independent() -> None:
    guidance = FileMemoryGuidance(system_preset="journal", custom="Only project milestones.", timezone="Asia/Taipei")
    resolved = resolve_guidance(guidance)
    assert "journal/YYYY-MM-DD.md" in resolved and "Only project milestones." in resolved
    overridden = guidance.model_copy(update={"system_override": "Use a monthly history."})
    assert "journal/YYYY-MM-DD.md" not in resolve_guidance(overridden)
    assert "Only project milestones." in resolve_guidance(overridden)
    assert "Use a monthly history." in resolve_guidance(overridden)
    assert "journal/YYYY-MM-DD.md" not in resolve_guidance(guidance.model_copy(update={"system_override": ""}))


async def test_accepted_runs_freeze_guidance_while_new_runs_get_edits(service, scripted_model, runs_kit):
    await runs_kit.pause_sweeps(service)
    space = await create_space(
        service, guidance={"system_preset": "journal", "custom": "Keep milestones.", "timezone": "Asia/Taipei"}
    )
    model = await runs_kit.create_model(service, scripted_model)
    agent = await runs_kit.add_agent(service, "Journal", model, memory={"enabled": True, "spaces": [binding(space)]})
    first = await runs_kit.start_thread(service, agent, "First")
    assert "file_guide" not in first["run"]["memory_mounts"][0]
    assert "timezone" not in first["run"]["memory_mounts"][0]
    frozen = await frozen_mount(service, first["run"]["id"])
    assert frozen["timezone"] == "Asia/Taipei"
    assert "journal/YYYY-MM-DD.md" in frozen["file_guide"]
    await patch(
        service,
        f"memory-spaces/{space['id']}",
        guidance={"system_override": "Use custom records.", "custom": "Keep outcomes.", "timezone": "UTC"},
    )
    old = await runs_kit.get_run(service, first["run"]["id"])
    assert old["memory_mounts"] == first["run"]["memory_mounts"]
    assert await frozen_mount(service, first["run"]["id"]) == frozen
    second = await runs_kit.start_thread(service, agent, "Second")
    current = await frozen_mount(service, second["run"]["id"])
    assert current["timezone"] == "UTC"
    assert "Use custom records." in current["file_guide"] and "Keep outcomes." in current["file_guide"]
    assert "journal/YYYY-MM-DD.md" not in current["file_guide"]
    # Execution adds stable scope and time-zone instructions without substituting another policy.
    planned = PlannedMemory(FrozenMemoryMount.model_validate(frozen), frozen["file_guide"], ())
    runtime_guide = _guide(planned, MemorySettings())
    assert frozen["file_guide"] in runtime_guide
    assert "Memory time zone: Asia/Taipei." in runtime_guide
    assert "beneath global/" in runtime_guide


async def frozen_mount(service, run_id):
    async with short_session(service.runtime.storage) as session:
        return dict((await session.get_one(RunRow, run_id)).memory_mounts[0])


def test_builtin_presets_do_not_consume_the_owner_authored_budget():
    guidance = FileMemoryGuidance(system_preset="journal")
    assert validate_guidance(guidance, max_bytes=256)["system_preset"] == "journal"
    with pytest.raises(ServiceError, match="owner-authored"):
        validate_guidance(guidance.model_copy(update={"custom": "字" * 86}), max_bytes=256)
    with pytest.raises(ServiceError, match="owner-authored"):
        validate_guidance(guidance.model_copy(update={"system_override": "x" * 200, "custom": "x" * 57}), max_bytes=256)


def test_missing_frozen_guidance_fails_instead_of_silently_omitting_rules():
    mount = FrozenMemoryMount(name="team", memory_id="mem_" + "a" * 32, access="write")
    with pytest.raises(ServiceError, match="missing frozen guidance"):
        _guide(PlannedMemory(mount, None, ()), MemorySettings())


async def test_file_memory_instructions_stay_stable_and_time_is_request_context(service, scripted_model, runs_kit):
    from .test_runs_memory import mount, team_memory, text

    memory = await team_memory(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    scripted_model.say("First")
    first = await runs_kit.start_thread(service, agent, "Read memory", memories=[mount(memory)])
    await (await runs_kit.attempt(service))
    first_request = await scripted_model.request()
    scripted_model.say("Second")
    await runs_kit.submit(service, first["thread"]["id"], runs_kit.message(agent, "Read again"))
    await (await runs_kit.attempt(service))
    second_request = await scripted_model.request()
    first_instructions = [m for m in first_request["messages"] if m["role"] in ("system", "developer")]
    second_instructions = [m for m in second_request["messages"] if m["role"] in ("system", "developer")]
    assert first_instructions == second_instructions
    assert "Memory time zone: UTC" in str(first_instructions)
    assert '"current_time":' not in str(first_instructions)
    assert '"current_time":' in text(first_request)
    assert '"current_time":' in text(second_request)
