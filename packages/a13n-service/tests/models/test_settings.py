import pytest
from a13n_service.models.model_apis import BUILT_IN_MODEL_APIS
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


@pytest.mark.parametrize("api", BUILT_IN_MODEL_APIS)
@pytest.mark.parametrize("thinking", [True, False, "minimal", "low", "medium", "high", "xhigh"])
def test_unified_thinking_is_available_on_every_api(api, thinking):
    assert validate_settings(api, {"thinking": thinking}) == {"thinking": thinking}


@pytest.mark.parametrize("api", BUILT_IN_MODEL_APIS)
@pytest.mark.parametrize(
    "alias",
    [
        "openai_reasoning_effort",
        "anthropic_thinking",
        "anthropic_effort",
        "google_thinking_config",
        "openrouter_reasoning",
    ],
)
def test_native_thinking_aliases_are_not_public_settings(api, alias):
    assert alias not in settings_schema(api)["properties"]
    with pytest.raises(ModelError):
        validate_settings(api, {alias: "high"})


@pytest.mark.parametrize("api", BUILT_IN_MODEL_APIS)
def test_thinking_uses_ordinary_model_agent_run_precedence(api):
    assert effective_settings(
        api,
        {"thinking": "low", "max_tokens": 2000},
        {"thinking": "high"},
        {"thinking": False, "max_tokens": 1000},
    ) == {"thinking": False, "max_tokens": 1000}
    assert effective_settings(api, {"thinking": "high"}, {"temperature": 0.2}) == {
        "thinking": "high",
        "temperature": 0.2,
    }


@pytest.mark.parametrize(
    ("api", "field", "path"),
    [
        (api, "extra_body", path)
        for api in BUILT_IN_MODEL_APIS
        for path in (
            ["reasoning"]
            if api.endswith("responses")
            else ["thinking", "output_config"]
            if api == "anthropic.messages"
            else []
            if api in {"google.generate_content", "bedrock.converse"}
            else ["reasoning", "reasoning_effort", "thinking", "enable_thinking"]
        )
    ]
    + [
        ("bedrock.converse", "bedrock_additional_model_requests_fields", path)
        for path in ["thinking", "output_config", "reasoning_effort", "reasoning_config"]
    ],
)
@pytest.mark.parametrize("value", [{}, None, "malformed", [], {"effort": "high"}])
def test_native_thinking_body_overrides_are_rejected(api, field, path, value):
    with pytest.raises(ModelError) as invalid:
        validate_settings(api, {field: {path: value}})
    assert invalid.value.details["reason"] == "reserved_request_field"


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


def test_effective_settings_does_not_share_mutable_input_layers() -> None:
    defaults = {"thinking": "low", "extra_body": {"custom": {"value": 1}}}
    overrides = {"thinking": "high", "extra_headers": {"x-title": "test"}}
    effective = effective_settings("openai.responses", defaults, overrides)
    effective["extra_body"]["custom"]["value"] = 2
    effective["extra_headers"]["x-title"] = "changed"
    assert defaults == {"thinking": "low", "extra_body": {"custom": {"value": 1}}}
    assert overrides == {"thinking": "high", "extra_headers": {"x-title": "test"}}
