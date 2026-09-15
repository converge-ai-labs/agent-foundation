from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.configuration.setup import SetupApiKeyModel, SetupSelection
from a13n_harness_ui.interactive.onboarding import run_setup
from a13n_harness_ui.interactive.setup import SetupWizard
from a13n_harness_ui.model_presets import API_MODEL_SUGGESTIONS, settings_presets
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings


@pytest.mark.parametrize(
    "provider,model_id,expected",
    [
        ("openai-responses", "gpt-5.6-sol", {"low": 16384, "medium": 32768, "high": 65536, "xhigh": 65536}),
        ("openai-chat", "gpt-5.4", {"low": 16384, "medium": 32768, "high": 65536, "xhigh": 65536}),
        ("anthropic", "claude-sonnet-4-6", {"adaptive": 32768, "interleaved": 16384}),
        ("anthropic", "claude-haiku-4-5", {"adaptive": 32768, "interleaved": 16384}),
        ("google", "gemini-2.5-pro", {"low": 16384, "medium": 32768, "high": 32768}),
        ("google", "gemini-3.5-flash", {"low": 16384, "medium": 32768, "high": 32768}),
        ("deepseek", "deepseek-v4-pro", {"thinking": 32768}),
        ("zai", "glm-5.3", {"thinking": 32768}),
        ("moonshotai", "kimi-k2.6", {"thinking": 32768}),
        ("openrouter", "openai/gpt-5.4", {"low": 16384, "medium": 32768, "high": 65536}),
        ("openrouter", "anthropic/claude-sonnet-4.6", {"low": 16384, "medium": 32768, "high": 32768}),
        ("openrouter", "google/gemini-2.5-pro", {"low": 16384, "medium": 32768, "high": 32768}),
    ],
)
def test_thinking_and_output_budgets_are_paired(provider, model_id, expected) -> None:
    presets = settings_presets(provider, model_id)
    assert {p.key: p.settings["max_tokens"] for p in presets if p.key != "default"} == expected
    default = next(p for p in presets if p.key == "default")
    assert "max_tokens" not in default.settings
    assert default.output_limit_label == "Output limit: provider default"
    for preset in presets:
        if preset.key in expected:
            assert preset.output_limit_label == f"Output limit: {expected[preset.key]:,} tokens"
        thinking = preset.settings.get("anthropic_thinking")
        if isinstance(thinking, dict) and thinking.get("type") == "enabled":
            assert preset.settings["max_tokens"] > thinking["budget_tokens"]


@pytest.mark.parametrize(
    "provider,model_id",
    [
        ("openai-responses", "gpt-5-custom"),
        ("openai-chat", "gpt-4.1"),
        ("openai-responses", "GPT-5.6-SOL"),
        ("google", "gemini-custom"),
        ("deepseek", "deepseek-chat"),
        ("zai", "glm-5-custom"),
        ("moonshotai", "kimi-k2-custom"),
        ("openrouter", "openai/gpt-5.4:free"),
        ("openrouter", "anthropic/claude-sonnet-4-6"),
        ("groq", "custom-model"),
        ("grok", "grok-4.6"),
        ("xai", "grok-4.6"),
        ("openai-codex", "gpt-5.6-sol"),
    ],
)
def test_unreviewed_routes_do_not_gain_output_budgets(provider, model_id) -> None:
    assert all("max_tokens" not in preset.settings for preset in settings_presets(provider, model_id))


def test_custom_anthropic_presets_retain_safe_existing_thinking_budget() -> None:
    presets = settings_presets("anthropic", "custom-claude")
    assert {p.key: p.settings.get("max_tokens") for p in presets} == {
        "interleaved": 16384,
        "adaptive": 16384,
        "default": None,
    }


def test_new_suggestions_do_not_certify_output_budgets(monkeypatch) -> None:
    monkeypatch.setitem(API_MODEL_SUGGESTIONS, "openai-responses", ("gpt-5-custom",))
    assert all("max_tokens" not in p.settings for p in settings_presets("openai-responses", "gpt-5-custom"))


def test_preset_settings_are_fresh_editable_values() -> None:
    first = settings_presets("anthropic", "claude-sonnet-4-6")[0]
    first.settings["max_tokens"] = 12345
    first.settings["anthropic_thinking"]["display"] = "omitted"
    second = settings_presets("anthropic", "claude-sonnet-4-6")[0]
    assert second.settings["max_tokens"] == 32768
    assert second.settings["anthropic_thinking"]["display"] == "summarized"


def test_backtracking_replaces_the_whole_preset_and_context_does_not_scale_output() -> None:
    wizard = SetupWizard(add_model=True)
    for answer in (
        "api",
        "openai-responses",
        "https://example.invalid/v1",
        "off",
        "env:TEST_KEY",
        "gpt-5.4",
        "high",
        "128k",
    ):
        wizard.accept(answer)
    assert wizard.question.key == "name"
    assert "Output limit: 65,536 tokens" in wizard.notice()
    while wizard.question.key != "preset":
        assert wizard.back()
    wizard.accept("low")
    wizard.accept("64k")
    assert "Output limit: 16,384 tokens" in wizard.notice()
    wizard.accept("Example")
    selection = SetupSelection.model_validate(wizard.selection("/tmp"))
    assert selection.api_key_model.settings["max_tokens"] == 16384
    assert selection.api_key_model.model_characteristics.context_window == 64000


@pytest.mark.anyio
@pytest.mark.parametrize("preset_key,expected", [("high", 65536), ("low", 16384), ("default", None)])
async def test_all_creation_paths_display_and_persist_paired_settings(tmp_path: Path, preset_key, expected) -> None:
    output: list[str] = []
    questions: list[str] = []
    reuse = False

    async def ask(question, selection):
        questions.append(question.key)
        if question.key == "preset":
            chosen = next(choice for choice in selection.choices if choice.value == preset_key)
            label = f"Output limit: {expected:,} tokens" if expected else "Output limit: provider default"
            assert label in chosen.description
        return {
            "model_source": "model-api-key" if reuse else "new",
            "provider": "api",
            "api_provider": "openai-responses",
            "base_url": "https://example.invalid/v1",
            "credential": "env:TEST_KEY",
            "model": "gpt-5.4",
            "preset": preset_key,
            "context": "128k",
            "tools": "none",
            "environment": "full-control",
            "name": "Output example",
        }.get(question.key, question.default)

    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")),
        configuration_path=tmp_path / "config.yaml",
    ) as app:
        for operation in ({}, {"add_model": True}, {"add_agent": True}):
            original = {path: path.read_bytes() for path in tmp_path.rglob("*.yaml")}
            assert await run_setup(app, tmp_path, ask_user=ask, emit=output.append, **operation), "\n".join(output)
            source = await app.current_configuration()
            assert all(model.settings.get("max_tokens") == expected for model in source.models.values())
            assert all(
                model.settings.get("thinking") == (None if expected is None else preset_key)
                for model in source.models.values()
            )
            assert all(path.read_bytes() == content for path, content in original.items())
            assert source.document.defaults.agent == "agent-api-key"
        assert questions.count("preset") == 3
        label = f"Output limit: {expected:,} tokens" if expected else "Output limit: provider default"
        assert sum(label in notice and "Settings:" in notice for notice in output) == 3

        original_models = {path: path.read_bytes() for path in (tmp_path / "models").glob("*.yaml")}
        questions.clear()
        reuse = True
        assert await run_setup(
            app, tmp_path, ask_user=ask, emit=output.append, add_agent=True, existing_model_id="model-api-key"
        )
        assert "preset" not in questions
        assert all(path.read_bytes() == content for path, content in original_models.items())


def test_direct_setup_settings_remain_authoritative() -> None:
    model = SetupApiKeyModel.model_validate(
        {
            "route": "openai-responses:gpt-5.4",
            "authentication": {"kind": "api_key", "env": "TEST_KEY"},
            "settings": {"thinking": "high", "max_tokens": 12345},
        }
    )
    assert model.settings == {"thinking": "high", "max_tokens": 12345}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider,model_id",
    [("google", "gemini-2.5-pro"), ("openai-chat", "gpt-5.4"), ("openrouter", "google/gemini-2.5-pro")],
)
async def test_output_budget_reaches_native_request_payload(provider, model_id) -> None:
    """Exercise upstream serialization, not live provider acceptance."""
    import json

    import httpx2 as httpx
    from pydantic_ai import Agent
    from pydantic_ai.models.google import GoogleModel
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.models.openrouter import OpenRouterModel
    from pydantic_ai.providers.google import GoogleProvider
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai.providers.openrouter import OpenRouterProvider
    from pydantic_ai.settings import ModelSettings

    payloads = []

    def respond(request):
        payloads.append(json.loads(request.content))
        body = (
            {"candidates": [{"content": {"role": "model", "parts": [{"text": "Done"}]}, "finishReason": "STOP"}]}
            if provider == "google"
            else {
                "id": "chat_test",
                "object": "chat.completion",
                "provider": "Google",
                "created": 1,
                "model": model_id,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "Done"}, "finish_reason": "stop"}],
            }
        )
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        if provider == "google":
            model = GoogleModel(model_id, provider=GoogleProvider(api_key="fixture", http_client=client))
        elif provider == "openrouter":
            model = OpenRouterModel(model_id, provider=OpenRouterProvider(api_key="fixture", http_client=client))
        else:
            model = OpenAIChatModel(model_id, provider=OpenAIProvider(api_key="fixture", http_client=client))
        settings = settings_presets(provider, model_id)[0].settings
        result = await Agent(model, model_settings=cast(ModelSettings, settings)).run("Hello")
    assert result.output == "Done"
    assert len(payloads) == 1
    if provider == "google":
        config = payloads[0]["generationConfig"]
        assert config["maxOutputTokens"] == 32768
        assert config["thinkingConfig"] == {"thinking_budget": 24576, "include_thoughts": True}
    elif provider == "openrouter":
        assert payloads[0]["max_tokens"] == 32768
        assert payloads[0]["reasoning"] == {"effort": "high", "exclude": False}
    else:
        assert payloads[0]["max_completion_tokens"] == 65536
        assert payloads[0]["reasoning_effort"] == "high"
