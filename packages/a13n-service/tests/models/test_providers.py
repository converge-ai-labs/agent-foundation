from __future__ import annotations

import httpx2
import pytest
from a13n_service.models.providers import ModelProviderMetadata, built_in_model_provider_catalog, validate_model_api
from a13n_service.models.service_common import ModelError


def test_registry_separates_provider_type_from_calling_api() -> None:
    registry = built_in_model_provider_catalog()

    assert ModelProviderMetadata.describe(registry.require("openai")).supported_model_apis == (
        "openai.responses",
        "openai.chat_completions",
    )
    assert ModelProviderMetadata.describe(registry.require("openrouter")).supported_model_apis == (
        "openrouter.chat_completions",
    )
    assert ModelProviderMetadata.describe(registry.require("ollama")).supported_model_apis == (
        "ollama.chat_completions",
    )
    assert ModelProviderMetadata.describe(registry.require("openai")).model_api_labels == {
        "openai.responses": "OpenAI Responses",
        "openai.chat_completions": "OpenAI Chat Completions",
    }


def test_registry_rejects_unbound_provider_api_combinations() -> None:
    registry = built_in_model_provider_catalog()

    with pytest.raises(ModelError, match="not supported"):
        validate_model_api(registry.require("openrouter"), "anthropic.messages")


def test_provider_config_does_not_select_a_calling_api() -> None:
    schema = ModelProviderMetadata.describe(
        built_in_model_provider_catalog().require("azure_openai")
    ).configuration_schema

    assert "api_protocol" not in schema["properties"]


def test_openai_auth_mode_controls_credential_requirement() -> None:
    registry = built_in_model_provider_catalog()

    validated = registry.require("openai").validate_configuration(
        {"base_url": "https://models.example/v1", "auth_mode": "none"},
        credential_configured=False,
    )
    assert validated.configuration["auth_mode"] == "none"

    with pytest.raises(ValueError, match="credential is required"):
        registry.require("openai").validate_configuration(
            {"base_url": "https://models.example/v1", "auth_mode": "bearer"},
            credential_configured=False,
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "credential", "expected_headers"),
    [
        ("none", None, {}),
        ("bearer", {"api_key": "secret"}, {"authorization": "Bearer secret"}),
        ("api_key_header", {"api_key": "secret"}, {"x-api-key": "secret"}),
    ],
)
async def test_openai_runtime_sends_only_selected_auth(
    mode: str,
    credential: dict[str, str] | None,
    expected_headers: dict[str, str],
) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"data": [], "object": "list"})

    configuration: dict[str, object] = {"base_url": "https://models.example/v1", "auth_mode": mode}
    if mode == "api_key_header":
        configuration["api_key_header_name"] = "x-api-key"
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        integration = built_in_model_provider_catalog().require("openai")
        provider = integration.build_provider(
            built_in_model_provider_catalog().require("openai").bind(configuration, credential),
            http,
            integration.supported_model_apis[0],
        )
        await provider.client.models.list()
    assert len(requests) == 1
    assert {
        key: requests[0].headers[key] for key in ("authorization", "x-api-key") if key in requests[0].headers
    } == expected_headers


def test_registry_has_one_openai_provider_for_both_apis() -> None:
    registry = built_in_model_provider_catalog()
    assert [item.type for item in registry.values()].count("openai") == 1
    assert ModelProviderMetadata.describe(registry.require("openai")).default_model_api == "openai.responses"
    assert ModelProviderMetadata.describe(registry.require("openai")).supported_model_apis == (
        "openai.responses",
        "openai.chat_completions",
    )


def test_help_and_probe_metadata_come_from_definitions():
    registry = built_in_model_provider_catalog()
    assert ModelProviderMetadata.describe(registry.require("anthropic")).supports_connection_probe
    assert (
        ModelProviderMetadata.describe(registry.require("anthropic")).setup_url
        == "https://platform.claude.com/settings/keys"
    )
    for name in ("google_vertex", "aws_bedrock"):
        assert not ModelProviderMetadata.describe(registry.require(name)).supports_connection_probe
        assert ModelProviderMetadata.describe(registry.require(name)).setup_url is not None
        assert ModelProviderMetadata.describe(registry.require(name)).setup_label is not None
    assert ModelProviderMetadata.describe(registry.require("ollama")).setup_url is None
    assert ModelProviderMetadata.describe(registry.require("ollama")).setup_label is None
