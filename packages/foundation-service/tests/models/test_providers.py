from __future__ import annotations

import pytest
from a13n_service.models.domain import ModelApiConfig
from a13n_service.models.providers import built_in_provider_registry


def test_registry_separates_provider_type_from_calling_api() -> None:
    registry = built_in_provider_registry()

    assert registry.definition("openai").supported_model_apis == (
        "openai.responses",
        "openai.chat_completions",
    )
    assert registry.definition("openrouter").supported_model_apis == ("openrouter.chat_completions",)
    assert registry.definition("ollama").supported_model_apis == ("ollama.chat_completions",)


def test_registry_rejects_unbound_provider_api_combinations() -> None:
    registry = built_in_provider_registry()

    with pytest.raises(ValueError, match="unsupported model APIs"):
        registry.validate_model_apis("openrouter", [ModelApiConfig(api="anthropic.messages")])


def test_provider_config_does_not_select_a_calling_api() -> None:
    schema = built_in_provider_registry().definition("azure_openai").config_schema

    assert "api_protocol" not in schema["properties"]


def test_openai_compatible_auth_mode_controls_credential_requirement() -> None:
    registry = built_in_provider_registry()

    validated = registry.validate_provider(
        "openai_compatible",
        {"base_url": "https://models.example/v1", "auth_mode": "none"},
        credential_configured=False,
    )
    assert validated.config["auth_mode"] == "none"

    with pytest.raises(ValueError, match="requires a credential"):
        registry.validate_provider(
            "openai_compatible",
            {"base_url": "https://models.example/v1", "auth_mode": "bearer"},
            credential_configured=False,
        )
