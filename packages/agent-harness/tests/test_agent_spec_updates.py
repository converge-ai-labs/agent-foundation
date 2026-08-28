from __future__ import annotations

import pytest
from a13n_harness import AgentSpec, ModelCapability, ModelConfiguration
from pydantic import ValidationError


def _preset() -> AgentSpec:
    return AgentSpec.from_dict(
        {
            "model": "openai:gpt-5",
            "name": "preset-agent",
            "instructions": "Use the preset behavior.",
            "model_settings": {"temperature": 0.2, "max_tokens": 2048},
            "metadata": {"preset": "research"},
            "capabilities": [{"WebSearch": {"native": True}}],
            "model_config": {
                "capabilities": ["image_understanding"],
                "context_window": 128000,
            },
        }
    )


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
            "model_config": {
                "capabilities": ["audio_understanding"],
                "context_window": 256000,
                "compact_threshold": 0.8,
            },
            "$schema": "./agent-schema.json",
        },
        toolset_instructions=False,
    )

    assert updated.model_configuration == ModelConfiguration(
        capabilities=frozenset({ModelCapability.AUDIO_UNDERSTANDING}),
        context_window=256000,
        compact_threshold=0.8,
    )
    assert updated.json_schema_path == "./agent-schema.json"
    assert updated.toolset_instructions is False
    assert preset.model_configuration == ModelConfiguration(
        capabilities=frozenset({ModelCapability.IMAGE_UNDERSTANDING}),
        context_window=128000,
    )


def test_with_updates_rejects_unknown_duplicate_and_invalid_fields() -> None:
    preset = _preset()

    with pytest.raises(ValueError, match="no updateable field"):
        preset.with_updates(temperature=0.1)
    with pytest.raises(ValueError, match="supplied more than once"):
        preset.with_updates({"model": "openai:gpt-5-mini"}, model="openai:gpt-5")
    with pytest.raises(ValueError, match="supplied through both"):
        preset.with_updates(
            {
                "model_configuration": ModelConfiguration(context_window=1000),
                "model_config": {"context_window": 2000},
            }
        )
    with pytest.raises(ValidationError):
        preset.with_updates(model_config={"context_window": 0})


def test_with_updates_does_not_recursively_merge_mapping_fields() -> None:
    preset = _preset()

    updated = preset.with_updates(model_settings={"temperature": 0.9})

    assert updated.model_settings == {"temperature": 0.9}
    assert "max_tokens" not in updated.model_settings
    assert preset.model_settings == {"temperature": 0.2, "max_tokens": 2048}
