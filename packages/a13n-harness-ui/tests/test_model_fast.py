"""Fast controls express requests, not provider entitlement or performance."""

import pytest
from a13n_harness_ui.errors import CompositionError
from a13n_harness_ui.model_fast import apply_fast, describe_fast, fast_state
from a13n_harness_ui.surfaces import RunModelOverrides
from pydantic import ValidationError


@pytest.mark.parametrize(
    "route", ["openai:gpt-5", "openai-responses:gpt-5.4", "openai-codex:gpt-5.5", "google-gla:gemini-2.5-pro"]
)
@pytest.mark.parametrize("selected", [True, False, None])
def test_fast_changes_only_request_settings(route, selected):
    settings = {"openai_service_tier": "priority", "thinking": "high", "max_tokens": 4096}
    effective = apply_fast(route, settings, selected)
    if selected is None:
        assert effective == settings and effective is not settings
    else:
        assert effective == {
            "service_tier": "priority" if selected else "default",
            "thinking": "high",
            "max_tokens": 4096,
        }
        assert fast_state(route, effective) == ("on" if selected else "off")
    assert settings["openai_service_tier"] == "priority"


@pytest.mark.parametrize("selected,expected", [(True, "fast"), (False, "standard")])
def test_anthropic_fast_is_speed_not_service_tier(selected, expected):
    settings = {
        "anthropic_speed": "fast",
        "anthropic_service_tier": "standard_only",
        "anthropic_thinking": {"type": "adaptive"},
    }
    effective = apply_fast("anthropic:claude-opus-4-8", settings, selected)
    assert effective == {**settings, "anthropic_speed": expected}
    assert fast_state("anthropic:claude-opus-4-8", effective) == ("on" if selected else "off")


@pytest.mark.parametrize(
    "settings,state",
    [
        ({}, "default"),
        ({"service_tier": "auto"}, "default"),
        ({"service_tier": "flex"}, "off"),
        ({"service_tier": "priority"}, "on"),
        ({"openai_service_tier": "fast"}, "on"),
        ({"service_tier": "priority", "openai_service_tier": "default"}, "off"),
    ],
)
def test_state_respects_native_precedence_and_unknown_defaults(settings, state):
    assert fast_state("openai:gpt-5", settings) == state


@pytest.mark.parametrize(
    "route",
    [
        "grok-build:grok-4",
        "anthropic:claude-sonnet-4-6",
        "anthropic:claude-opus-4-6",
        "anthropic:claude-opus-4-7",
        "google-vertex:gemini-2.5-pro",
        "openai:custom-model",
    ],
)
def test_unreviewed_connections_reject_override_but_preserve_defaults(route):
    assert not describe_fast(route, {}).supported
    for selected in (True, False):
        with pytest.raises(CompositionError, match="Fast controls"):
            apply_fast(route, {}, selected)
    assert apply_fast(route, {"custom": True}, None) == {"custom": True}


@pytest.mark.parametrize(
    "settings",
    [
        {"extra_body": {"service_tier": "priority"}},
        {"extra_body": {"speed": "fast"}},
        {"extra_headers": {"X-Gemini-Service-Tier": "priority"}},
    ],
)
def test_custom_request_conflicts_cannot_make_a_toggle_ineffective(settings):
    assert not describe_fast("openai:gpt-5", settings).supported
    assert fast_state("openai:gpt-5", settings) == "default"
    with pytest.raises(CompositionError):
        apply_fast("openai:gpt-5", settings, False)
    assert apply_fast("openai:gpt-5", settings, None) == settings


def test_fast_and_raw_tier_overrides_are_unambiguous():
    with pytest.raises(ValidationError, match="either fast or service_tier"):
        RunModelOverrides(fast=False, service_tier="priority")
    assert RunModelOverrides(fast=False).fast is False
