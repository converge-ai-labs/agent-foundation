from dataclasses import replace

import pytest
from a13n_harness_ui.model_presets import API_MODEL_SUGGESTIONS, API_PROVIDER_BY_ROUTE, API_PROVIDERS
from a13n_harness_ui.resource_names import coding_agent_name, model_name


@pytest.mark.parametrize(
    "provider,model_id",
    [
        *((provider.route, model) for provider in API_PROVIDERS for model in API_MODEL_SUGGESTIONS[provider.route]),
        ("codex", "gpt-6-astra"),
        ("grok-subscription", "grok-4.6"),
    ],
)
def test_every_recommended_connection_generates_plain_printable_names(provider, model_id) -> None:
    model = model_name(provider, model_id)
    agent = coding_agent_name(model)
    assert model.isascii() and model.isprintable()
    assert agent.isascii() and agent.isprintable()
    assert agent == f"{model} - Coding"
    assert len(model) <= 110 and len(agent) <= 128


def test_resource_names_do_not_copy_terminal_provider_labels(monkeypatch) -> None:
    provider = API_PROVIDER_BY_ROUTE["together"]
    monkeypatch.setitem(
        API_PROVIDER_BY_ROUTE,
        provider.route,
        replace(provider, label="\x1b[31mTogether · Featured provider\x1b[0m"),
    )
    assert model_name("together", "Qwen/CustomID") == "Together AI - Qwen/CustomID"
    assert model_name("custom-provider", "CustomID") == "custom-provider - CustomID"


def test_plain_generated_formatting_preserves_custom_unicode_and_case() -> None:
    assert model_name("openai-chat", "组织/模型-AbC") == "OpenAI Chat - 组织/模型-AbC"
    assert coding_agent_name("我的模型") == "我的模型 - Coding"
    assert len(coding_agent_name("x" * 256)) <= 128
