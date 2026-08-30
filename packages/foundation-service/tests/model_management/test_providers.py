import pytest
from a13n_service.model_management.domain import (
    ModelCapabilities,
    NoCredential,
    WorkspaceSecretCredential,
)
from a13n_service.model_management.providers import built_in_provider_registry
from pydantic import ValidationError


def credential() -> WorkspaceSecretCredential:
    return WorkspaceSecretCredential(secret_id="sec_1234567890abcdef")


def test_registry_contains_exact_accepted_provider_composition() -> None:
    registry = built_in_provider_registry()

    assert tuple(item.key for item in registry.definitions()) == (
        "openai",
        "anthropic",
        "google_gemini",
        "google_vertex",
        "azure_openai",
        "aws_bedrock",
        "alibaba_model_studio",
        "deepseek",
        "moonshot",
        "zhipu",
        "openai_compatible",
    )


def test_public_registry_contains_schemas_but_no_credentials() -> None:
    registry = built_in_provider_registry()
    serialized = "".join(item.model_dump_json() for item in registry.definitions())

    assert "credential_schema" in serialized
    assert "connection_schema" in serialized
    assert "secret_id" in serialized
    assert "secret_key" in serialized
    assert "sec_1234567890abcdef" not in serialized


def test_official_provider_rejects_arbitrary_connection_fields() -> None:
    registry = built_in_provider_registry()

    with pytest.raises(ValidationError, match="base_url"):
        registry.validate(
            provider_type="openai",
            model_name="gpt-5.6-sol",
            provider_config={"base_url": "https://proxy.example.com"},
            credential=credential(),
            capabilities=None,
        )


def test_unknown_model_name_is_valid_and_has_unknown_advisory_capabilities() -> None:
    selected = built_in_provider_registry().validate(
        provider_type="openai",
        model_name="future-model",
        provider_config={},
        credential=credential(),
        capabilities=None,
    )

    assert selected.capability_source == "catalog"
    assert selected.capabilities == ModelCapabilities()


def test_domestic_provider_catalogs_ship_as_advisory_metadata() -> None:
    definitions = {item.key: item for item in built_in_provider_registry().definitions()}

    assert {item.model_name for item in definitions["alibaba_model_studio"].model_catalog} >= {
        "qwen3.8-max",
        "qwen3.7-plus",
    }
    assert {item.model_name for item in definitions["deepseek"].model_catalog} >= {"deepseek-v4-pro"}
    assert {item.model_name for item in definitions["moonshot"].model_catalog} >= {"kimi-k2.5"}
    assert {item.model_name for item in definitions["zhipu"].model_catalog} >= {"glm-5.2"}


def test_alibaba_region_and_workspace_derive_only_trusted_endpoints() -> None:
    registry = built_in_provider_registry()
    selected = registry.validate(
        provider_type="alibaba_model_studio",
        model_name="qwen3.8-max",
        provider_config={"region": "cn-beijing", "domain_type": "mainland_china"},
        credential=credential(),
        capabilities=None,
    )
    assert selected.base_url is None

    with pytest.raises(ValidationError, match="requires alibaba_workspace_id"):
        registry.validate(
            provider_type="alibaba_model_studio",
            model_name="qwen3.7-plus",
            provider_config={"region": "eu-central-1", "domain_type": "international"},
            credential=credential(),
            capabilities=None,
        )


def test_manual_capabilities_override_catalog_without_becoming_a_gate() -> None:
    manual = ModelCapabilities(input_modalities=("text",), tool_calling=False)
    selected = built_in_provider_registry().validate(
        provider_type="openai",
        model_name="gpt-5.6-sol",
        provider_config={},
        credential=credential(),
        capabilities=manual,
    )

    assert selected.capability_source == "manual_override"
    assert selected.capabilities is manual


def test_openai_compatible_requires_a_well_formed_header_mode() -> None:
    registry = built_in_provider_registry()

    with pytest.raises(ValidationError, match="api_key_header_name is required"):
        registry.validate(
            provider_type="openai_compatible",
            model_name="self-hosted",
            provider_config={"base_url": "https://models.example.com/v1", "auth_mode": "api_key_header"},
            credential=credential(),
            capabilities=None,
        )

    selected = registry.validate(
        provider_type="openai_compatible",
        model_name="self-hosted",
        provider_config={
            "base_url": "https://models.example.com/v1/",
            "auth_mode": "api_key_header",
            "api_key_header_name": "X-API-Key",
        },
        credential=credential(),
        capabilities=None,
    )
    assert selected.base_url == "https://models.example.com/v1/"

    with pytest.raises(ValidationError, match="reserved by HTTP"):
        registry.validate(
            provider_type="openai_compatible",
            model_name="self-hosted",
            provider_config={
                "base_url": "https://models.example.com/v1",
                "auth_mode": "api_key_header",
                "api_key_header_name": "Host",
            },
            credential=credential(),
            capabilities=None,
        )


def test_azure_openai_accepts_only_normalized_official_endpoints() -> None:
    registry = built_in_provider_registry()

    selected = registry.validate(
        provider_type="azure_openai",
        model_name="deployment-name",
        provider_config={"resource_endpoint": "https://example.openai.azure.com"},
        credential=credential(),
        capabilities=None,
    )

    assert selected.base_url == "https://example.openai.azure.com/openai/v1"
    assert selected.provider_config["resource_endpoint"] == selected.base_url

    with pytest.raises(ValidationError, match="official Azure model domain"):
        registry.validate(
            provider_type="azure_openai",
            model_name="deployment-name",
            provider_config={"resource_endpoint": "https://attacker.example.com"},
            credential=credential(),
            capabilities=None,
        )


def test_providers_fail_closed_for_disallowed_credential_modes() -> None:
    with pytest.raises(ValueError, match="credential source 'none'"):
        built_in_provider_registry().validate(
            provider_type="deepseek",
            model_name="deepseek-v4-pro",
            provider_config={},
            credential=NoCredential(),
            capabilities=None,
        )


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown provider"):
        built_in_provider_registry().definition("caller_import_path")
