"""Every managed recipe exposes ordinary defaults and retains saved target identities."""

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_service.providers.environments import BUILT_IN_ENVIRONMENT_PROVIDERS

INPUTS = {
    "local": {"root": {"path": "/tmp/managed"}, "allowed_environment_keys": ["TOOL_ENDPOINT"]},
    "docker": {},
    "e2b": {},
    "daytona": {"snapshot": "custom-envd"},
    "modal": {"image": "im-custom"},
    "runloop": {},
    "sprites": {},
    "vercel": {},
}


@pytest.mark.parametrize("definition", BUILT_IN_ENVIRONMENT_PROVIDERS, ids=lambda item: item.type)
def test_recipe_defaults_roundtrip_and_empty_compatibility(definition):
    inputs = INPUTS[definition.type]
    original = definition.validate_environment(inputs)
    empty = definition.validate_environment({**inputs, "environment": {}})
    configured = definition.validate_environment({**inputs, "environment": {"TOOL_ENDPOINT": "https://example.test"}})
    assert empty == original
    assert "environment" in definition.environment_model.model_json_schema()["properties"]
    restored = definition.validate_environment(configured.model_dump(mode="json"))
    assert restored.environment == {"TOOL_ENDPOINT": "https://example.test"}
    execution = definition.execution_environment(restored)
    assert execution.environment == restored.environment
    if hasattr(original, "fingerprint"):
        assert original.fingerprint == empty.fingerprint
        assert configured.fingerprint != original.fingerprint
        assert restored.fingerprint == execution.fingerprint


@pytest.mark.parametrize(
    "key,value",
    [
        ("BAD=NAME", "x"),
        ("9INVALID", "x"),
        ("VALID", "bad\x00value"),
        ("A13N_ENVD_HTTP_BIND", "x"),
        ("eip_token", "x"),
        ("A13N_DESKTOP_ACCESS", "x"),
        ("OTEL_EXPORTER_OTLP_ENDPOINT", "x"),
        ("DISPLAY", "x"),
    ],
)
@pytest.mark.parametrize("definition", BUILT_IN_ENVIRONMENT_PROVIDERS, ids=lambda item: item.type)
def test_invalid_and_platform_owned_variables_fail_before_allocation(definition, key, value):
    with pytest.raises(EnvironmentProviderError) as error:
        definition.validate_environment({**INPUTS[definition.type], "environment": {key: value}})

    assert error.value.code == "provider_spec_invalid"
