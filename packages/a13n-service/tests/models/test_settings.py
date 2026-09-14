import pytest
from a13n_service.models.candidates import candidate_from_catalog
from a13n_service.models.model_apis import BUILT_IN_MODEL_APIS
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.service_common import ModelError
from a13n_service.models.settings import effective_settings, settings_schema, validate_settings
from jsonschema import Draft202012Validator


@pytest.mark.parametrize("api", BUILT_IN_MODEL_APIS)
def test_every_binding_has_a_self_contained_native_settings_contract(api: str) -> None:
    schema = settings_schema(api)
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["temperature"]["type"] == "number"
    assert "description" in schema["properties"]["temperature"]
    assert validate_settings(api, {"temperature": 0.3}) == {"temperature": 0.3}

    def refs(value):
        if isinstance(value, dict):
            if "$ref" in value:
                assert value["$ref"].startswith("#/")
            for child in value.values():
                refs(child)
        elif isinstance(value, list):
            for child in value:
                refs(child)

    refs(schema)
    assert validate_settings(api, {}) == {}


@pytest.mark.parametrize(
    "settings",
    [
        {"temperature": "hot"},
        {"temperature": True},
        {"max_tokens": -1},
        {"unknown": 1},
        {"openrouter_models": ["other"]},
        {"openrouter_preset": "other"},
        {"extra_body": {"model": "other"}},
        {"extra_body": {"tools": []}},
        {"extra_headers": {"authorization": "secret"}},
        {"extra_headers": {"host": "evil.example"}},
        {"openrouter_provider": {"only": "not-an-array"}},
        {"openrouter_provider": {"mystery": True}},
    ],
)
def test_invalid_and_reserved_settings_are_rejected(settings: dict) -> None:
    with pytest.raises(ModelError) as invalid:
        validate_settings("openrouter.chat_completions", settings)
    assert invalid.value.code == "invalid_model_settings"
    assert invalid.value.details["path"][0] == "settings"
    assert "secret" not in str(invalid.value.details)


def test_bounds_apply_to_objects_and_effective_merge() -> None:
    with pytest.raises(ModelError):
        validate_settings("openai.responses", {"extra_body": {"new_parameter": "中" * 23000}})
    nested = {}
    for _ in range(16):
        nested = {"nested": nested}
    with pytest.raises(ModelError):
        validate_settings("openai.responses", {"extra_body": nested})
    with pytest.raises(ModelError):
        effective_settings("openai.responses", {"openai_user": "x" * 40000}, {"extra_body": {"new": "y" * 40000}})


def test_top_level_merge_replaces_routing_and_retains_other_defaults() -> None:
    result = effective_settings(
        "openrouter.chat_completions",
        {"max_tokens": 300, "openrouter_provider": {"only": ["a"], "allow_fallbacks": False}},
        {"openrouter_provider": {"only": ["b"]}},
    )
    assert result == {"max_tokens": 300, "openrouter_provider": {"only": ["b"]}}
    assert validate_settings("openrouter.chat_completions", {"extra_body": {"new_parameter": {"value": 1}}})


def test_catalog_candidates_are_advisory() -> None:
    registry = built_in_provider_registry()
    unknown = candidate_from_catalog(registry, "openai", "unreleased/deployment")
    assert unknown.suggested_model_api == "openai.responses"
    assert unknown.profile.input_modalities is None
    assert unknown.profile.supports_json_schema_output is True
    assert unknown.suggested_settings == {}
    assert unknown.parameter_support == {}
    known = candidate_from_catalog(
        registry,
        "openrouter",
        "team/model",
        metadata={
            "supported_parameters": ["temperature", "tools", "reasoning"],
            "architecture": {"input_modalities": ["text", "image"]},
            "context_length": 128000,
            "top_provider": {"max_completion_tokens": 16000},
            "default_parameters": {"temperature": 0.7, "max_tokens": "bad", "model": "other"},
        },
    )
    assert known.suggested_settings == {"temperature": 0.7}
    assert known.profile.supports_tools is True
    assert known.profile.supports_thinking is True
    assert known.limits.context_window_tokens == 128000
    assert known.parameter_support["/openrouter_provider"] == "supported"
    assert known.parameter_support["/seed"] == "unsupported"
    assert known.parameter_support.get("/extra_body", "unknown") == "unknown"


def test_malformed_optional_catalog_metadata_keeps_trusted_candidate() -> None:
    result = candidate_from_catalog(
        built_in_provider_registry(),
        "openrouter",
        "team/model",
        metadata={
            "supported_parameters": ["tools"],
            "architecture": "unknown",
            "top_provider": [],
            "context_length": True,
        },
    )
    assert result.profile.supports_tools is True
    assert result.profile.input_modalities is None
    assert result.limits.context_window_tokens is None
    assert result.limits.max_output_tokens is None


def test_openrouter_catalog_facts_override_gateway_wide_profile_defaults() -> None:
    result = candidate_from_catalog(
        built_in_provider_registry(),
        "openrouter",
        "unknown/model",
        metadata={"supported_parameters": ["tools"]},
    )
    assert result.profile.supports_tools is True
    assert result.profile.supports_thinking is False


def test_native_provider_profile_is_projected_without_catalog_metadata() -> None:
    result = candidate_from_catalog(built_in_provider_registry(), "openai", "gpt-5")
    assert result.profile.supports_thinking is True
    assert result.profile.thinking_always_enabled is True


def test_explicit_reasoning_choice_replaces_conflicting_inherited_settings() -> None:
    assert effective_settings(
        "openai.responses",
        {
            "openai_reasoning_effort": "low",
            "temperature": 0.2,
            "extra_body": {"metadata": {"source": "model"}},
        },
        {"thinking": "high", "max_tokens": 1000},
    ) == {
        "thinking": "high",
        "temperature": 0.2,
        "max_tokens": 1000,
        "extra_body": {"metadata": {"source": "model"}},
    }
    assert effective_settings(
        "openai.responses",
        {"thinking": "medium", "extra_body": {"metadata": {"source": "model"}}},
        {"extra_body": {"reasoning": {"effort": "high"}, "metadata": {"source": "run"}}},
    ) == {"extra_body": {"reasoning": {"effort": "high"}, "metadata": {"source": "run"}}}


def test_reasoning_precedence_is_applied_across_each_override_layer() -> None:
    assert effective_settings(
        "openai.responses",
        {"extra_body": {"reasoning": {"effort": "minimal"}}, "max_tokens": 2000},
        {"openai_reasoning_effort": "low", "temperature": 0.2},
        {"thinking": "high", "max_tokens": 1000},
    ) == {"thinking": "high", "temperature": 0.2, "max_tokens": 1000}


def test_same_layer_reasoning_alternatives_are_rejected() -> None:
    with pytest.raises(ModelError) as invalid:
        validate_settings(
            "openrouter.chat_completions", {"thinking": "high", "openrouter_reasoning": {"effort": "low"}}
        )
    assert invalid.value.details == {
        "path": ["settings", "openrouter_reasoning"],
        "reason": "conflicting_reasoning_settings",
    }


def test_complementary_native_anthropic_reasoning_settings_can_coexist() -> None:
    settings = {"anthropic_thinking": {"type": "adaptive"}, "anthropic_effort": "high"}
    assert validate_settings("anthropic.messages", settings) == settings


@pytest.mark.parametrize("output_config", [{}, None, "malformed", []])
def test_bedrock_unified_thinking_replaces_suppressing_output_config(output_config) -> None:
    defaults = {
        "bedrock_additional_model_requests_fields": {
            "output_config": output_config,
            "unrelated": "preserved",
        }
    }
    assert effective_settings("bedrock.converse", defaults, {"thinking": "high"}) == {
        "bedrock_additional_model_requests_fields": {"unrelated": "preserved"},
        "thinking": "high",
    }


def test_bedrock_native_thinking_and_effort_can_coexist() -> None:
    settings = {
        "bedrock_additional_model_requests_fields": {
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "high"},
        }
    }
    assert validate_settings("bedrock.converse", settings) == settings


@pytest.mark.parametrize("api", BUILT_IN_MODEL_APIS)
def test_missing_source_documentation_does_not_disable_parameter_validation(monkeypatch, api):
    import inspect

    def unavailable(_):
        raise OSError("source is not installed")

    settings_schema.cache_clear()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(inspect, "getsource", unavailable)
            schema = settings_schema(api)
            assert schema["properties"]["temperature"]["type"] == "number"
            assert validate_settings(api, {"temperature": 0.5}) == {"temperature": 0.5}
            with pytest.raises(ModelError):
                validate_settings(api, {"temperature": "invalid"})
    finally:
        settings_schema.cache_clear()


@pytest.mark.parametrize(
    ("api", "body"),
    [
        ("openai.chat_completions", {"tool_choice": "none"}),
        ("openai.responses", {"text": {"format": {"type": "text"}}}),
        ("openai.responses", {"text": None}),
        ("openai.responses", {"text": []}),
        ("anthropic.messages", {"output_config": {"format": {"type": "json_schema"}}}),
        ("anthropic.messages", {"output_config": "replace"}),
        ("openrouter.chat_completions", {"models": ["different"]}),
        ("bedrock.converse", {"toolConfig": {}}),
    ],
)
def test_only_explicit_protocol_paths_are_protected(api, body):
    field = "bedrock_additional_model_requests_fields" if api == "bedrock.converse" else "extra_body"
    with pytest.raises(ModelError) as invalid:
        validate_settings(api, {field: body})
    assert invalid.value.details["reason"] == "reserved_request_field"


@pytest.mark.parametrize(
    "api",
    [
        "openai.responses",
        "openai.chat_completions",
        "openrouter.chat_completions",
        "anthropic.messages",
        "bedrock.converse",
    ],
)
def test_extensions_are_opaque_and_native_collisions_are_upstream_owned(api):
    field = "bedrock_additional_model_requests_fields" if api == "bedrock.converse" else "extra_body"
    settings = {
        "temperature": 0.2,
        "stop_sequences": ["END"],
        field: {
            "temperature": "upstream-decides",
            "custom": {"model": "label", "stop": "nested-data", "schema": {"format": "value"}},
        },
    }
    assert validate_settings(api, settings) == settings


def test_sibling_of_protected_nested_path_is_free_to_use():
    # Acceptance does not promise preservation of text.format on the wire:
    # the native SDK shallow-merges extra_body and replaces the whole text object.
    settings = {"extra_body": {"text": {"verbosity": "brief"}}}
    assert validate_settings("openai.responses", settings) == settings
