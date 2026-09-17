"""Native account forms derive from the credential and configuration validators."""

import pytest
from a13n_service.connectivity.providers.registry import built_in_ingress_adapter_registry
from pydantic import ValidationError


def test_registered_account_metadata_exposes_safe_typed_fields():
    registry = built_in_ingress_adapter_registry()
    for key, version in registry.versions():
        definition = registry.create(key, config_version=version).describe_account(config_version=version)
        assert definition.provider_key == key
        assert definition.config_version == version
        assert definition.configuration_schema["additionalProperties"] is False
        assert definition.credential_schema["additionalProperties"] is False
        assert definition.credential_schema["required"]
        assert "default" not in definition.credential_schema
        assert definition.target_kinds == (("repository",) if key == "github" else ("conversation",))


def test_slack_credential_schema_limits_are_enforced_by_the_same_model():
    adapter = built_in_ingress_adapter_registry().create("slack", config_version="slack_http_v1")
    definition = adapter.describe_account(config_version="slack_http_v1")
    maximum = next(
        option["maxLength"]
        for option in definition.credential_schema["properties"]["signing_secret"]["anyOf"]
        if option.get("type") == "string"
    )
    assert (
        adapter.validate_credentials(
            {"signing_secret": "s" * maximum, "bot_token": "token"}, config_version="slack_http_v1"
        )["bot_token"]
        == "token"
    )
    with pytest.raises(ValidationError):
        adapter.validate_credentials(
            {"signing_secret": "s" * (maximum + 1), "bot_token": "token"}, config_version="slack_http_v1"
        )
