"""Guidance ownership, override semantics, clock context and acceptance freezing."""

from datetime import UTC, datetime

import pytest
from a13n_service.resources.memories.guidance import FileMemoryGuidance, resolve_guidance, time_context
from a13n_service.runs.memories.execution import PlannedMemory, _guide
from a13n_service.runs.memories.schemas import FrozenMemoryMount
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
    assert time_context("Asia/Taipei", datetime(2026, 11, 5, 18, tzinfo=UTC)).endswith("2026-11-06T02:00:00+08:00.")


async def test_accepted_runs_freeze_guidance_while_new_runs_get_edits(service, scripted_model, runs_kit):
    await runs_kit.pause_sweeps(service)
    space = await create_space(
        service, guidance={"system_preset": "journal", "custom": "Keep milestones.", "timezone": "Asia/Taipei"}
    )
    model = await runs_kit.create_model(service, scripted_model)
    agent = await runs_kit.add_agent(service, "Journal", model, memory={"enabled": True, "spaces": [binding(space)]})
    first = await runs_kit.start_thread(service, agent, "First")
    frozen = first["run"]["memory_mounts"][0]
    assert frozen["timezone"] == "Asia/Taipei"
    assert "journal/YYYY-MM-DD.md" in frozen["file_guide"]
    await patch(
        service,
        f"memory-spaces/{space['id']}",
        guidance={"system_override": "Use custom records.", "custom": "Keep outcomes.", "timezone": "UTC"},
    )
    old = await runs_kit.get_run(service, first["run"]["id"])
    assert old["memory_mounts"][0] == frozen
    second = await runs_kit.start_thread(service, agent, "Second")
    current = second["run"]["memory_mounts"][0]
    assert current["timezone"] == "UTC"
    assert "Use custom records." in current["file_guide"] and "Keep outcomes." in current["file_guide"]
    assert "journal/YYYY-MM-DD.md" not in current["file_guide"]
    # Execution adds scope and current clock data without substituting another policy.
    planned = PlannedMemory(FrozenMemoryMount.model_validate(frozen), frozen["file_guide"], ())
    runtime_guide = _guide(planned, MemorySettings())
    assert frozen["file_guide"] in runtime_guide
    assert "Memory time zone: Asia/Taipei." in runtime_guide
    assert "beneath global/" in runtime_guide
