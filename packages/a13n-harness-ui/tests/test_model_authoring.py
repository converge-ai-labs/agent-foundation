"""The common authoring contract expands defaults without composing a Model."""

import pytest
from a13n_harness.spec import HarnessModelCharacteristics
from a13n_harness_ui.configuration.models import ApiKeyAuthentication
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.model_authoring import (
    ModelChoices,
    ModelOptionsRequest,
    ModelRecipe,
    ModelRecipeRequest,
    model_options,
    prepare_model,
    recipe_document,
)
from pydantic import ValidationError


def test_subscription_defaults_are_native_and_independent_of_api_presets() -> None:
    codex = prepare_model(ModelRecipeRequest(connection="codex", model_id="gpt-5.6-sol"))
    assert codex.authentication.kind == "codex_subscription"
    assert codex.route == "openai-codex:gpt-5.6-sol"
    assert codex.settings == {
        "thinking": "high",
        "openai_reasoning_summary": "detailed",
        "openai_store": False,
        "openai_service_tier": "priority",
    }
    assert codex.model_configuration == {}
    assert codex.model_characteristics.context_window_tokens == 350000
    grok = prepare_model(ModelRecipeRequest(connection="grok-subscription", model_id="grok-4.7"))
    assert grok.authentication.kind == "grok_subscription"
    assert grok.route == "grok:grok-4.7"
    assert grok.settings == {} and grok.model_configuration == {}
    assert grok.model_characteristics.context_window_tokens is None
    choices = ModelChoices()
    assert next(c for c in choices.connections if c.id == "grok").authentication == "api_key"
    subscription = next(c for c in choices.connections if c.id == "grok-subscription")
    assert subscription.authentication == "grok_subscription"
    assert subscription.default_model == "grok-4.7"
    assert subscription.models[0].value == subscription.default_model


def test_prepare_preserves_explicit_native_mappings_and_empty_capabilities() -> None:
    recipe = prepare_model(
        ModelRecipeRequest(
            connection="openai-chat",
            model_id="deployment:model",
            authentication=ApiKeyAuthentication(kind="api_key", credential_ref="key-local"),
            settings={"temperature": 0.2, "extra_body": {"custom": True}},
            model_configuration={"base_url": "http://localhost:8080/v1", "session_affinity_header": "x-session-id"},
            model_characteristics=HarnessModelCharacteristics(context_window_tokens=32000, capabilities=frozenset()),
        )
    )
    assert recipe.model_configuration["base_url"] == "http://localhost:8080/v1"
    assert recipe.settings == {"temperature": 0.2, "extra_body": {"custom": True}}
    assert recipe_document(recipe)["model_characteristics"]["capabilities"] == []
    assert recipe_document(recipe)["model_characteristics"]["context_window_tokens"] == 32000
    assert prepare_model(ModelRecipeRequest(connection="codex", model_id="old-model", settings={})).settings == {}


def test_unknown_ids_have_conservative_recommendations_and_remain_editable() -> None:
    for connection in ("codex", "grok-subscription", "openai-chat"):
        options = model_options(ModelOptionsRequest(connection=connection, model_id="custom-model"))
        assert len(options.presets) == 1
        assert "thinking" not in options.presets[0].settings
        assert options.name
    with pytest.raises(HarnessUiError, match="whitespace"):
        model_options(ModelOptionsRequest(connection="codex", model_id="bad model"))


def test_authentication_endpoint_and_preset_validation() -> None:
    with pytest.raises(HarnessUiError, match="authentication"):
        prepare_model(ModelRecipeRequest(connection="openai-chat", model_id="custom"))
    with pytest.raises(HarnessUiError, match="authentication"):
        prepare_model(
            ModelRecipeRequest(
                connection="codex",
                model_id="custom",
                authentication=ApiKeyAuthentication(kind="api_key", env="MY_API_KEY"),
            )
        )
    with pytest.raises(HarnessUiError, match="consistent"):
        prepare_model(
            ModelRecipeRequest(
                connection="openai-chat",
                model_id="custom",
                base_url="http://localhost:8080",
                model_configuration={"base_url": "http://localhost:9090"},
            )
        )
    with pytest.raises(HarnessUiError, match="native endpoint"):
        prepare_model(ModelRecipeRequest(connection="codex", model_id="custom", base_url="http://localhost:8080"))
    with pytest.raises(HarnessUiError, match="setting offered"):
        prepare_model(ModelRecipeRequest(connection="codex", model_id="custom", preset="xhigh"))
    with pytest.raises(ValidationError, match="native route"):
        ModelRecipe.model_validate({"route": "openai-chat:custom", "authentication": {"kind": "codex_subscription"}})


def test_terminal_manual_subscription_id_uses_only_offered_defaults() -> None:
    from a13n_harness_ui.interactive.setup import SetupWizard

    wizard = SetupWizard(add_model=True)
    for answer in ("codex", "old-subscription-model", "off", "Custom subscription"):
        wizard.accept(answer)
    recipe = wizard.selection("/tmp")["model"]
    assert recipe["route"] == "openai-codex:old-subscription-model"
    assert recipe["settings"] == {"openai_store": False, "openai_service_tier": "default"}


def test_terminal_saved_credential_choice_never_needs_the_secret() -> None:
    from a13n_harness_ui.interactive.setup import SetupWizard

    wizard = SetupWizard(add_model=True, saved_credentials=("key-existing",))
    for answer in ("api", "openai-chat", "", "key:key-existing", "custom:model", "default", "32k", "Local model"):
        wizard.accept(answer)
    assert wizard.question is None
    recipe = wizard.selection("/tmp")["model"]
    assert recipe["authentication"]["credential_ref"] == "key-existing"
    assert recipe["route"] == "openai-chat:custom:model"
    assert recipe["model_characteristics"]["context_window_tokens"] == 32000


@pytest.mark.parametrize("explicit_capabilities", [False, True])
def test_prepare_json_roundtrip_materializes_only_omitted_capabilities(explicit_capabilities: bool) -> None:
    from a13n_harness_ui.configuration.setup import SetupSelection

    policy = {"context_window_tokens": 32000, "compact_threshold": 0.8}
    if explicit_capabilities:
        policy["capabilities"] = []
    prepared = prepare_model(
        ModelRecipeRequest.model_validate(
            {
                "connection": "codex",
                "model_id": "gpt-5.6-sol",
                "model_characteristics": policy,
            }
        )
    )
    selection = SetupSelection.model_validate(
        {
            "model": prepared.model_dump(mode="json"),
            "environment_profile": "environment-native",
        }
    )
    saved = recipe_document(selection.model)["model_characteristics"]
    assert saved["capabilities"] == ([] if explicit_capabilities else ["image_understanding"])
    assert saved["context_window_tokens"] == 32000
    assert saved["compact_threshold"] == 0.8


def test_model_options_describe_agent_tools_without_mutating_model_recipe() -> None:
    options = model_options(ModelOptionsRequest(connection="codex", model_id="gpt-5.6-sol"))
    search = next(tool for tool in options.native_tools if tool.value == "web_search")
    assert search.recommended and search.replaces_host_operation == "search"
    assert search.capability == {
        "capability": "NativeTool",
        "configuration": {"kind": "web_search", "external_web_access": True},
    }
    image = next(tool for tool in options.native_tools if tool.value == "image_generation")
    assert image.capability["capability"] == "native_image_generation"
    custom = model_options(
        ModelOptionsRequest(connection="openai-responses", model_id="custom", base_url="http://localhost:8080/v1")
    )
    assert all(not tool.recommended for tool in custom.native_tools)
    assert next(tool for tool in custom.native_tools if tool.value == "mcp_server").capability is None
    recipe = prepare_model(ModelRecipeRequest(connection="codex", model_id="gpt-5.6-sol"))
    assert "native_tools" not in recipe.model_dump()
