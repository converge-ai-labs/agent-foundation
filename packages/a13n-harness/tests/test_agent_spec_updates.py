from __future__ import annotations

from pathlib import Path

import pytest
from a13n_harness import (
    AgentDefinition,
    AgentSpec,
    DefinitionError,
    HarnessModelCharacteristics,
    ModelCapability,
    SubagentDefinition,
)
from a13n_harness.capabilities import UserInteractionCapability
from a13n_harness.filters import ColdStartFilterConfiguration
from pydantic import ValidationError
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import UsageLimits


def _preset() -> AgentSpec:
    return AgentSpec.from_dict(
        {
            "model": "openai:gpt-5",
            "name": "preset-agent",
            "instructions": "Use the preset behavior.",
            "model_settings": {"temperature": 0.2, "max_tokens": 2048},
            "metadata": {"preset": "research"},
            "capabilities": [{"WebSearch": {"native": True}}],
            "model_characteristics": {
                "capabilities": ["image_understanding"],
                "context_window_tokens": 128000,
            },
        }
    )


@pytest.mark.parametrize("idle_seconds", [None, 3600, 7200])
def test_cold_start_configuration_round_trip_and_updates(tmp_path: Path, idle_seconds: int | None) -> None:
    default = AgentSpec()
    assert default.cold_start_filter == ColdStartFilterConfiguration()
    assert default.cold_start_filter is not AgentSpec().cold_start_filter
    spec = default.with_updates(cold_start_filter={"idle_seconds": idle_seconds} if idle_seconds is not None else None)
    path = tmp_path / "agent.json"
    spec.to_file(path)
    restored = AgentSpec.from_file(path)
    assert restored.cold_start_filter == spec.cold_start_filter
    assert default.cold_start_filter.idle_seconds == 3600
    assert spec.with_updates(name="copy").cold_start_filter == spec.cold_start_filter
    schema = AgentSpec.model_json_schema_with_capabilities()
    assert schema["properties"]["cold_start_filter"]["default"]["idle_seconds"] == 3600
    assert "ColdStartFilterConfiguration" in schema["$defs"]
    with pytest.raises(ValidationError):
        spec.with_updates(cold_start_filter={"idle_seconds": 0})


def test_agent_spec_defaults_to_a_detached_long_task_usage_budget() -> None:
    first = AgentSpec()
    second = AgentSpec()

    assert first.usage_limits.request_limit == 1000
    assert first.usage_limits is not second.usage_limits

    first.usage_limits.request_limit = 3
    assert second.usage_limits.request_limit == 1000


def test_agent_spec_schema_and_updates_preserve_native_usage_limits() -> None:
    spec = AgentSpec.from_dict(
        {
            "usage_limits": {
                "request_limit": 250,
                "total_tokens_limit": 100_000,
            }
        }
    )
    updated = spec.with_updates(usage_limits=UsageLimits(request_limit=None))
    schema = AgentSpec.model_json_schema_with_capabilities()

    assert spec.usage_limits.request_limit == 250
    assert spec.usage_limits.total_tokens_limit == 100_000
    assert updated.usage_limits.request_limit is None
    assert updated.usage_limits is not spec.usage_limits
    assert schema["properties"]["usage_limits"]["default"]["request_limit"] == 1000


def test_with_updates_returns_a_validated_deep_copy_without_mutating_preset() -> None:
    preset = _preset()

    updated = preset.with_updates(
        model="anthropic:claude-sonnet-4-6",
        instructions=["Use the preset behavior.", "Prefer primary sources."],
        model_settings={"temperature": 0.1},
        metadata=None,
    )

    assert updated is not preset
    assert updated.model == "anthropic:claude-sonnet-4-6"
    assert updated.instructions == ["Use the preset behavior.", "Prefer primary sources."]
    assert updated.model_settings == {"temperature": 0.1}
    assert updated.metadata is None
    assert preset.model == "openai:gpt-5"
    assert preset.instructions == "Use the preset behavior."
    assert preset.model_settings == {"temperature": 0.2, "max_tokens": 2048}
    assert preset.metadata == {"preset": "research"}
    assert updated.capabilities is not preset.capabilities
    assert updated.capabilities[0] is not preset.capabilities[0]


def test_with_updates_accepts_field_aliases_and_dynamic_mapping_input() -> None:
    preset = _preset()

    updated = preset.with_updates(
        {
            "model_characteristics": {
                "capabilities": ["audio_understanding"],
                "context_window_tokens": 256000,
                "compact_threshold": 0.8,
            },
            "$schema": "./agent-schema.json",
        },
        toolset_instructions=False,
    )

    assert updated.model_characteristics == HarnessModelCharacteristics(
        capabilities=frozenset({ModelCapability.AUDIO_UNDERSTANDING}),
        context_window_tokens=256000,
        compact_threshold=0.8,
    )
    assert updated.json_schema_path == "./agent-schema.json"
    assert updated.toolset_instructions is False
    assert preset.model_characteristics == HarnessModelCharacteristics(
        capabilities=frozenset({ModelCapability.IMAGE_UNDERSTANDING}),
        context_window_tokens=128000,
    )


def test_with_updates_rejects_unknown_duplicate_and_invalid_fields() -> None:
    preset = _preset()

    with pytest.raises(ValueError, match="no updateable field"):
        preset.with_updates(temperature=0.1)
    with pytest.raises(ValueError, match="supplied more than once"):
        preset.with_updates({"model": "openai:gpt-5-mini"}, model="openai:gpt-5")
    with pytest.raises(ValidationError):
        preset.with_updates(model_characteristics={"context_window_tokens": 0})


def test_with_updates_does_not_recursively_merge_mapping_fields() -> None:
    preset = _preset()

    updated = preset.with_updates(model_settings={"temperature": 0.9})

    assert updated.model_settings == {"temperature": 0.9}
    assert "max_tokens" not in updated.model_settings
    assert preset.model_settings == {"temperature": 0.2, "max_tokens": 2048}


def test_agent_definition_with_updates_materializes_a_complete_child() -> None:
    model = TestModel()
    capability = UserInteractionCapability()
    nested = AgentDefinition(
        agent=AgentSpec(system_prompt="Nested prompt."),
        output_type=str,
        definition_id="nested",
        model=model,
    )
    parent = AgentDefinition(
        agent=AgentSpec(system_prompt="Parent prompt."),
        output_type=str,
        definition_id="parent",
        model=model,
        capabilities=(capability,),
        subagents=(SubagentDefinition(name="nested", description="Nested child", agent=nested),),
    )

    child_spec = parent.agent.with_updates(system_prompt="Child prompt.")
    child = parent.with_updates(
        agent=child_spec,
        definition_id="child",
        capabilities=[capability],
        subagents=[],
    )

    assert child is not parent
    assert child.definition_id == "child"
    assert child.agent.system_prompt == "Child prompt."
    assert child.agent is not child_spec
    assert child.agent is not parent.agent
    assert child.model is model
    assert child.capabilities == (capability,)
    assert child.capabilities[0] is capability
    assert child.subagents == ()
    assert child.output_type is parent.output_type
    assert child.model_recovery is parent.model_recovery
    assert parent.definition_id == "parent"
    assert parent.agent.system_prompt == "Parent prompt."
    assert len(parent.subagents) == 1


def test_agent_definition_with_updates_retains_omitted_topology_and_id() -> None:
    nested = AgentDefinition(agent=AgentSpec(), output_type=str, definition_id="nested")
    edge = SubagentDefinition(name="nested", description="Nested child", agent=nested)
    parent = AgentDefinition(
        agent=AgentSpec(system_prompt="Parent prompt."),
        output_type=str,
        definition_id="parent",
        subagents=(edge,),
    )

    updated = parent.with_updates(agent=parent.agent.with_updates(system_prompt="Updated prompt."))

    assert updated.definition_id == "parent"
    assert updated.subagents == (edge,)
    assert updated.subagents[0] is edge


def test_agent_definition_with_updates_rejects_invalid_inputs_and_revalidates() -> None:
    definition = AgentDefinition(agent=AgentSpec(), output_type=str, definition_id="base")

    with pytest.raises(TypeError, match="mapping with string keys"):
        definition.with_updates([("definition_id", "child")])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="supplied more than once"):
        definition.with_updates({"definition_id": "child"}, definition_id="other")
    with pytest.raises(ValueError, match="no updateable field"):
        definition.with_updates(parent="base")
    with pytest.raises(DefinitionError) as exc_info:
        definition.with_updates(definition_id=" ")

    assert exc_info.value.code == "definition_id_invalid"
