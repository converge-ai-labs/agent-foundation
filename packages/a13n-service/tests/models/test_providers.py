from __future__ import annotations

import httpx2
import pytest
from a13n_service.models.provider_adapters.types import RuntimeProvider
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.service_common import ModelError


def test_registry_separates_provider_type_from_calling_api() -> None:
    registry = built_in_provider_registry()

    assert registry.definition("openai").supported_model_apis == (
        "openai.responses",
        "openai.chat_completions",
    )
    assert registry.definition("openrouter").supported_model_apis == ("openrouter.chat_completions",)
    assert registry.definition("ollama").supported_model_apis == ("ollama.chat_completions",)
    assert registry.definition("openai").model_api_labels == {
        "openai.responses": "OpenAI Responses",
        "openai.chat_completions": "OpenAI Chat Completions",
    }


def test_registry_rejects_unbound_provider_api_combinations() -> None:
    registry = built_in_provider_registry()

    with pytest.raises(ModelError, match="not supported"):
        registry.validate_model_api("openrouter", "anthropic.messages")


def test_provider_config_does_not_select_a_calling_api() -> None:
    schema = built_in_provider_registry().definition("azure_openai").configuration_schema

    assert "api_protocol" not in schema["properties"]


def test_openai_auth_mode_controls_credential_requirement() -> None:
    registry = built_in_provider_registry()

    validated = registry.validate_provider(
        "openai",
        {"base_url": "https://models.example/v1", "auth_mode": "none"},
        credential_configured=False,
    )
    assert validated.configuration["auth_mode"] == "none"

    with pytest.raises(ValueError, match="requires a credential"):
        registry.validate_provider(
            "openai",
            {"base_url": "https://models.example/v1", "auth_mode": "bearer"},
            credential_configured=False,
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "credential", "expected_headers"),
    [
        ("none", None, {}),
        ("bearer", "secret", {"authorization": "Bearer secret"}),
        ("api_key_header", "secret", {"x-api-key": "secret"}),
    ],
)
async def test_openai_runtime_sends_only_selected_auth(
    mode: str,
    credential: str | None,
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
        integration = built_in_provider_registry().integration("openai")
        provider = integration.build_provider(
            RuntimeProvider("openai", configuration, "https://models.example/v1", credential),
            http,
            integration.supported_model_apis[0],
        )
        await provider.client.models.list()
    assert len(requests) == 1
    assert {
        key: requests[0].headers[key] for key in ("authorization", "x-api-key") if key in requests[0].headers
    } == expected_headers


def test_registry_has_one_openai_provider_for_both_apis() -> None:
    registry = built_in_provider_registry()
    assert [item.type for item in registry.definitions()].count("openai") == 1
    assert registry.definition("openai").default_model_api == "openai.responses"
    assert registry.definition("openai").supported_model_apis == ("openai.responses", "openai.chat_completions")
