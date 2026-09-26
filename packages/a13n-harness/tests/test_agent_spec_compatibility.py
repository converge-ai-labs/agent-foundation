"""Frozen definition input belongs to Harness; UI persistence has its own thin regression."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, HarnessState, ModelCapability, RunBindings
from a13n_harness.tools import ToolIdentity, ToolPermissionsCapability
from pydantic import ValidationError
from pydantic_ai.capabilities import IncludeToolReturnSchemas, SetToolMetadata, Thinking
from pydantic_ai.messages import ModelResponse, UserPromptPart

from .test_output_schema import output_model

FIXTURE = Path(__file__).parent / "fixtures/compatibility/agent_spec_legacy_context_window.json"


def _canonical_spec() -> AgentSpec:
    raw = json.loads(FIXTURE.read_bytes())
    characteristics = raw["model_characteristics"]
    characteristics["context_window_tokens"] = characteristics.pop("context_window")
    return AgentSpec.from_dict(raw)


def test_fixed_legacy_spelling_requires_host_normalization() -> None:
    with pytest.raises(ValidationError, match="context_window"):
        AgentSpec.from_file(FIXTURE)


def test_fixed_agent_spec_preserves_declared_fields_after_host_normalization(tmp_path: Path) -> None:
    original = FIXTURE.read_bytes()
    raw = json.loads(original)
    spec = _canonical_spec()
    serialized = spec.model_dump(mode="json", by_alias=True)
    # Never synthesize this input from the current model or replace its old spelling.
    assert raw["model_characteristics"]["context_window"] == 128000
    for field in (
        "$schema",
        "model",
        "name",
        "description",
        "instructions",
        "system_prompt",
        "toolset_instructions",
        "cold_start_filter",
        "model_settings",
        "deps_schema",
        "output_schema",
        "retries",
        "end_strategy",
        "tool_timeout",
        "metadata",
    ):
        assert serialized[field] == raw[field], field
    for field, value in raw["usage_limits"].items():
        assert serialized["usage_limits"][field] == value, field
    assert spec.usage_limits.request_limit == 12
    characteristics = spec.model_characteristics
    assert characteristics is not None
    assert characteristics.context_window_tokens == 128000
    assert characteristics.summary_reminder_tokens == 64000
    assert characteristics.compact_threshold == 0.8
    assert characteristics.capabilities == {
        ModelCapability.IMAGE_UNDERSTANDING,
        ModelCapability.AUDIO_UNDERSTANDING,
        ModelCapability.VIDEO_UNDERSTANDING,
    }
    assert "context_window" not in serialized["model_characteristics"]
    assert serialized["model_characteristics"]["context_window_tokens"] == 128000
    assert [(item.name, item.arguments) for item in spec.capabilities] == [
        ("Thinking", {"effort": "low"}),
        ("IncludeToolReturnSchemas", None),
        ("SetToolMetadata", {"tools": ["echo"], "compatibility": {"fixture": True}}),
        (
            "ToolPermissionsCapability",
            {"default": "deny", "rules": {"environment.*": "ask", "environment.read": "allow"}},
        ),
    ]
    target = tmp_path / "agent.json"
    spec.to_file(target, schema_path=None)
    assert AgentSpec.from_file(target) == spec
    assert FIXTURE.read_bytes() == original


@pytest.mark.anyio
async def test_fixed_agent_spec_builds_capabilities_and_resumes_without_external_services() -> None:
    spec = _canonical_spec()
    first_output = {"summary": "first", "score": 1}
    second_output = {"summary": "second", "score": 2}
    first_calls: list[int] = []

    async def first_model(context, model_id):
        assert model_id == "openai:gpt-5"
        return output_model([first_output], first_calls)

    executable = HarnessBuilder().build(spec, output_type=None)
    leaves = []
    executable._agent.root_capability.apply(leaves.append)
    thinking = next(item for item in leaves if isinstance(item, Thinking))
    assert thinking.get_model_settings() == {"thinking": "low"}
    assert next(item for item in leaves if isinstance(item, IncludeToolReturnSchemas)).tools == "all"
    metadata = next(item for item in leaves if isinstance(item, SetToolMetadata))
    assert metadata.tools == ["echo"]
    assert metadata.metadata == {"compatibility": {"fixture": True}}
    permissions = next(item for item in leaves if isinstance(item, ToolPermissionsCapability)).permissions
    assert permissions.resolve(ToolIdentity("environment.read", "deny")) == "allow"
    assert permissions.resolve(ToolIdentity("environment.write", "allow")) == "ask"
    assert permissions.resolve(ToolIdentity("other.tool", "allow")) == "deny"
    first = await executable.run("first", bindings=RunBindings.embedded(model_resolver=first_model))
    assert first.output_or_raise() == first_output
    assert first_calls == [0]
    assert first.state is not None
    restored = HarnessState.model_validate_json(first.state.model_dump_json())
    second_calls: list[int] = []

    async def second_model(context, model_id):
        assert model_id == "openai:gpt-5"
        return output_model([second_output], second_calls)

    rebuilt = HarnessBuilder().build(_canonical_spec(), output_type=None)
    second = await rebuilt.run(
        "second", bindings=RunBindings.embedded(model_resolver=second_model), previous_state=restored
    )
    assert second.output_or_raise() == second_output
    assert second_calls == [0]
    assert second.thread_id == first.thread_id
    assert second.run_id != first.run_id
    # Host context and system prompts can be reprojected; retained conversation
    # content, rather than transient request metadata, is the compatibility rule.
    first_response = next(message for message in first.all_messages() if isinstance(message, ModelResponse))
    assert first_response in second.all_messages()
    assert any(
        isinstance(part, UserPromptPart) and part.content == "first"
        for message in second.all_messages()
        for part in message.parts
    )
    assert second.state is not None
    assert second.state.thread_id == restored.thread_id
