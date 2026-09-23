from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from a13n_harness_ui.errors import CompositionError
from a13n_harness_ui.model_thinking import apply_thinking, describe_thinking, summarize_thinking


@pytest.mark.parametrize(
    "route, expected",
    [
        ("openai-responses:gpt-5", [None, "minimal", "low", "medium", "high"]),
        ("openai-codex:gpt-5.6-sol", [None, False, "low", "medium", "high", "xhigh"]),
        ("openai-codex:gpt-6-sol", [None, False, "low", "medium", "high", "xhigh"]),
        ("anthropic:claude-sonnet-4-6", [None, False, "low", "medium", "high"]),
        ("anthropic:claude-opus-4-6", [None, False, "low", "medium", "high", "max"]),
        ("google:gemini-3-pro-preview", [None, "low", "high"]),
        ("google:gemini-3.1-pro-preview", [None, "low", "medium", "high"]),
        ("google:gemini-2.5-flash", [None, False, "low", "medium", "high"]),
        ("google:gemini-2.5-pro", [None, "low", "medium", "high"]),
        ("anthropic:custom-model", [None]),
        ("openai-chat:custom-model", [None]),
        ("openai-chat:gpt-4o", [None]),
    ],
)
def test_model_specific_options(route, expected):
    control = describe_thinking(route, {})
    assert [option.value for option in control.options] == expected
    assert control.status == (
        "supported" if len(expected) > 1 else "unsupported" if route.endswith("gpt-4o") else "unknown"
    )


@pytest.mark.parametrize(
    "route,settings,expected",
    [
        ("openai:gpt-5.4", {"thinking": "low", "openai_reasoning_effort": "high"}, "High"),
        (
            "anthropic:claude-sonnet-4-6",
            {"thinking": "low", "anthropic_thinking": {"type": "adaptive"}, "anthropic_effort": "high"},
            "Adaptive · High",
        ),
        (
            "anthropic:claude-haiku-4-5",
            {"anthropic_thinking": {"type": "enabled", "budget_tokens": 8192}},
            "Budget · 8,192 tokens",
        ),
        ("google:gemini-3-pro-preview", {"google_thinking_config": {"thinking_level": "LOW"}}, "Low"),
        ("google:gemini-2.5-flash", {"google_thinking_config": {"thinking_budget": 0}}, "Off"),
        ("google:gemini-2.5-flash", {"thinking": "high"}, "High"),
        (
            "google:gemini-2.5-flash",
            {"thinking": "high", "google_thinking_config": {"include_thoughts": True}},
            "Provider default",
        ),
        ("openai:gpt-5.4", {"openai_reasoning_effort": "arbitrary secret string"}, "Custom"),
    ],
)
def test_native_settings_are_the_display_authority(route, settings, expected):
    assert summarize_thinking(route, settings) == expected
    assert describe_thinking(route, settings).default_summary == expected


def test_override_preserves_siblings_and_default_restores_opaque_configuration():
    settings = {
        "anthropic_thinking": {"type": "enabled", "budget_tokens": 8192, "display": "summarized"},
        "anthropic_effort": "high",
        "max_tokens": 16384,
        "extra_headers": {"x-example": "kept"},
    }
    original = deepcopy(settings)
    effective = apply_thinking("anthropic:claude-sonnet-4-6", settings, "low")
    assert effective["anthropic_thinking"] == {"type": "adaptive", "display": "summarized"}
    assert effective["anthropic_effort"] == "low"
    assert effective["max_tokens"] == 16384
    assert effective["extra_headers"] == settings["extra_headers"]
    assert settings == original
    assert apply_thinking("anthropic:claude-sonnet-4-6", settings, None) == original


def test_google_override_removes_budget_without_losing_summary_setting():
    settings = {"google_thinking_config": {"thinking_budget": 8192, "include_thoughts": False}}
    assert apply_thinking("google:gemini-3.1-pro-preview", settings, "medium")["google_thinking_config"] == {
        "thinking_level": "MEDIUM",
        "include_thoughts": False,
    }


def test_budget_option_is_disabled_and_rejected_without_changing_output_cap():
    settings = {"max_tokens": 16384}
    route = "anthropic:claude-haiku-4-5"
    high = next(option for option in describe_thinking(route, settings).options if option.value == "high")
    assert "max_tokens" in high.disabled_reason
    with pytest.raises(CompositionError, match="max_tokens"):
        apply_thinking(route, settings, "high")
    assert apply_thinking(route, settings, "low")["max_tokens"] == 16384


@pytest.mark.parametrize(
    "route,selection",
    [
        ("openai:gpt-5", False),
        ("google:gemini-3-pro-preview", "medium"),
        ("google:gemini-3.1-pro-preview", False),
        ("anthropic:custom", "low"),
    ],
)
def test_invalid_selections_never_fall_back(route, selection):
    with pytest.raises(CompositionError) as error:
        apply_thinking(route, {}, selection)
    assert error.value.code == "thinking_selection_invalid"


def test_extra_body_conflicts_are_visible_and_do_not_rewrite_authored_settings():
    settings = {"extra_body": {"reasoning": {"effort": "high"}}}
    control = describe_thinking("openai:gpt-5.4", settings)
    assert control.default_summary == "Custom (extra_body)"
    assert all(option.disabled_reason for option in control.options if option.value is not None)
    with pytest.raises(CompositionError, match="extra_body"):
        apply_thinking("openai:gpt-5.4", settings, "low")
    assert apply_thinking("openai:gpt-5.4", settings, None) == settings


@pytest.mark.anyio
@pytest.mark.parametrize("provider", ["openai-chat", "openai-responses", "anthropic", "google"])
async def test_native_sdk_request_receives_the_selected_value(provider, monkeypatch):
    """Exercise real SDK translation, stopping at the native client's I/O seam."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart
    from pydantic_ai.models import ModelRequestParameters

    send = AsyncMock(side_effect=RuntimeError("captured native request"))
    if provider.startswith("openai"):
        from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
        from pydantic_ai.providers.openai import OpenAIProvider

        native = OpenAIProvider(api_key="test-not-a-secret")
        if provider == "openai-chat":
            model = OpenAIChatModel("gpt-5.4", provider=native)
            monkeypatch.setattr(model.client.chat.completions, "create", send)
        else:
            model = OpenAIResponsesModel("gpt-5.4", provider=native)
            monkeypatch.setattr(model.client.responses, "create", send)
        route = f"{provider}:gpt-5.4"
        settings = {"openai_reasoning_effort": "high", "openai_reasoning_summary": "detailed"}
    elif provider == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        model = AnthropicModel("claude-sonnet-4-6", provider=AnthropicProvider(api_key="test-not-a-secret"))
        monkeypatch.setattr(model.client.beta.messages, "create", send)
        route = "anthropic:claude-sonnet-4-6"
        settings = {
            "anthropic_thinking": {"type": "adaptive", "display": "summarized"},
            "anthropic_effort": "high",
            "max_tokens": 16384,
        }
    else:
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        model = GoogleModel("gemini-3.1-pro-preview", provider=GoogleProvider(api_key="test-not-a-secret"))
        monkeypatch.setattr(model.client.aio.models, "generate_content", send)
        route = "google:gemini-3.1-pro-preview"
        settings = {"google_thinking_config": {"thinking_level": "HIGH", "include_thoughts": True}}
    effective = apply_thinking(route, settings, "low")
    async with model:
        with pytest.raises(RuntimeError, match="captured native request"):
            await model.request([ModelRequest(parts=[UserPromptPart("Hello")])], effective, ModelRequestParameters())
    send.assert_awaited_once()
    payload = send.call_args.kwargs
    if provider == "openai-chat":
        assert payload["reasoning_effort"] == "low"
    elif provider == "openai-responses":
        assert payload["reasoning"]["effort"] == "low"
        assert payload["reasoning"]["summary"] == "detailed"
    elif provider == "anthropic":
        assert payload["thinking"] == {"type": "adaptive", "display": "summarized"}
        assert payload["output_config"]["effort"] == "low"
        assert payload["max_tokens"] == 16384
    else:
        assert payload["config"]["thinking_config"] == {"thinking_level": "LOW", "include_thoughts": True}
