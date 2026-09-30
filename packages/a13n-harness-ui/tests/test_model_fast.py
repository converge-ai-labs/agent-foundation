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


@pytest.mark.parametrize(
    "selected,tier,state",
    [("ultrafast", "ultrafast", "ultrafast"), (True, "priority", "on"), (False, "default", "off")],
)
def test_codex_ultrafast_selection_replaces_native_tier_without_changing_other_controls(selected, tier, state):
    from a13n_harness_ui.model_controls import ModelControlSelection, apply_model_controls

    route = "openai-codex:gpt-6-astra"
    settings = {"openai_service_tier": "ultrafast", "service_tier": "flex", "openai_reasoning_summary": "detailed"}
    selection = ModelControlSelection(fast=selected, thinking="high")
    assert selection.controls().fast == selected
    effective = apply_model_controls(route, settings, selection)
    assert effective == {
        "service_tier": tier,
        "openai_reasoning_summary": "detailed",
        "thinking": "high",
        "openai_reasoning_effort": "high",
    }
    assert fast_state(route, effective) == state
    assert describe_fast(route, settings).ultrafast_supported
    assert apply_fast(route, settings, None) == settings
    assert fast_state(route, settings) == "ultrafast"


@pytest.mark.parametrize(
    "route",
    [
        "openai:gpt-6-astra",
        "openai-responses:gpt-6-astra",
        "openai-codex:gpt-6-sol",
        "openai-codex:gpt-6.1-sol",
        "openai-codex:gpt-5.5",
        "anthropic:claude-opus-4-8",
        "google-gla:gemini-2.5-pro",
    ],
)
def test_ultrafast_is_not_a_generic_fast_or_gpt_prefix_capability(route):
    control = describe_fast(route, {})
    assert control.supported
    assert not control.ultrafast_supported
    assert "Codex subscription" in control.ultrafast_reason
    with pytest.raises(CompositionError, match="Ultrafast requires"):
        apply_fast(route, {}, "ultrafast")
    assert apply_fast(route, {}, True)


def test_ultrafast_respects_conflicts_and_strict_override_validation():
    route = "openai-codex:gpt-6-astra"
    settings = {"extra_body": {"service_tier": "priority"}}
    assert not describe_fast(route, settings).ultrafast_supported
    with pytest.raises(CompositionError):
        apply_fast(route, settings, "ultrafast")
    with pytest.raises(ValidationError, match="either fast or service_tier"):
        RunModelOverrides(fast="ultrafast", service_tier="priority")
    for invalid in ("true", "on", "priority", "ultra", 1):
        with pytest.raises(ValidationError):
            RunModelOverrides.model_validate({"fast": invalid})
    assert RunModelOverrides.model_validate_json('{"fast":"ultrafast"}').fast == "ultrafast"


@pytest.mark.anyio
@pytest.mark.parametrize("selected,tier", [("ultrafast", "ultrafast"), (True, "priority"), (False, "default")])
async def test_codex_native_request_serializes_speed_and_routing_hint(selected, tier):
    import json
    from types import SimpleNamespace
    from typing import cast
    from unittest.mock import AsyncMock

    import httpx2
    from a13n_harness.models.codex import CodexRequestModel
    from a13n_harness_ui.model_controls import ModelControlSelection, apply_model_controls
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.providers.openai_codex import OpenAICodexCredentials
    from pydantic_ai.settings import ModelSettings

    requests = []
    response = {
        "id": "resp_fixture",
        "created_at": 1,
        "model": "gpt-6-astra",
        "object": "response",
        "output": [],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }
    events = [
        {"type": "response.created", "sequence_number": 0, "response": {**response, "status": "in_progress"}},
        {"type": "response.completed", "sequence_number": 1, "response": {**response, "status": "completed"}},
    ]

    def respond(request):
        requests.append(request)
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join(f"data: {json.dumps(event)}\n\n" for event in events).encode(),
        )

    source = SimpleNamespace(
        load=AsyncMock(
            return_value=OpenAICodexCredentials(
                account_id="fixture", access_token="fixture-access", refresh_token="fixture-refresh"
            )
        ),
        save=AsyncMock(),
    )
    settings = apply_model_controls(
        "openai-codex:gpt-6-astra",
        {"openai_service_tier": "ultrafast", "openai_reasoning_summary": "detailed"},
        ModelControlSelection(fast=selected, thinking="high"),
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        model = CodexRequestModel(
            "gpt-6-astra", credential_source=source, http_client=client, thread_id="thread-fixture"
        )
        async with model.request_stream([], cast(ModelSettings, settings), ModelRequestParameters()) as stream:
            async for _ in stream:
                pass
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body["service_tier"] == tier
    assert body["model"] == "gpt-6-astra"
    assert body["reasoning"]["effort"] == "high"
    assert body["reasoning"]["summary"] == "detailed"
    assert requests[0].headers["x-codex-routing-hint"] == f"model=gpt-6-astra;tier={tier}"
    source.save.assert_not_called()
