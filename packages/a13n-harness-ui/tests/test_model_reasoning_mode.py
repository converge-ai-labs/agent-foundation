"""Local request intent, not live provider support or account entitlement."""

import pytest
from a13n_harness_ui.errors import CompositionError
from a13n_harness_ui.model_controls import ModelControlSelection, apply_model_controls, describe_model_controls
from a13n_harness_ui.model_reasoning_mode import apply_reasoning_mode, describe_reasoning_mode
from a13n_harness_ui.webui import SubmitRequest


@pytest.mark.parametrize("provider", ["openai", "openai-responses", "openai-codex"])
@pytest.mark.parametrize("selected", ["standard", "pro", None])
def test_mode_is_independent_and_does_not_mutate_model(provider, selected):
    route = f"{provider}:gpt-5.6-sol"
    settings = {"openai_reasoning_mode": "pro", "openai_reasoning_summary": "detailed", "thinking": "high"}
    effective = apply_model_controls(route, settings, ModelControlSelection(reasoning_mode=selected, fast=False))
    assert effective == {**settings, "openai_reasoning_mode": selected or "pro", "service_tier": "default"}
    assert settings == {"openai_reasoning_mode": "pro", "openai_reasoning_summary": "detailed", "thinking": "high"}
    assert describe_model_controls(route, settings).reasoning_mode.state == "pro"


@pytest.mark.parametrize(
    "route", ["openai-chat:gpt-5.6-sol", "openai:gpt-5.4", "anthropic:claude-opus-4-8", "openai:custom"]
)
def test_unsupported_transport_or_profile_rejects_only_explicit_selection(route):
    settings = {"openai_reasoning_mode": "pro"}
    assert not describe_reasoning_mode(route, settings).supported
    for mode in ("standard", "pro"):
        with pytest.raises(CompositionError, match="not available"):
            apply_reasoning_mode(route, settings, mode)
    assert apply_reasoning_mode(route, settings, None) == settings


@pytest.mark.parametrize("reasoning", [None, "pro", {}, {"mode": "pro"}, {"mode": None}, {"summary": "detailed"}])
def test_extra_body_cannot_silently_replace_explicit_mode(reasoning):
    settings = {"extra_body": {"reasoning": reasoning}}
    control = describe_reasoning_mode("openai:gpt-5.6-sol", settings)
    assert not control.supported
    assert control.state == "custom"
    with pytest.raises(CompositionError, match="extra_body"):
        apply_reasoning_mode("openai:gpt-5.6-sol", settings, "standard")
    assert apply_reasoning_mode("openai:gpt-5.6-sol", settings, None) == settings


@pytest.mark.parametrize(
    "value,state", [(None, "default"), ("standard", "standard"), ("pro", "pro"), ("unknown", "custom")]
)
def test_default_description_does_not_invent_standard(value, state):
    settings = {"openai_reasoning_mode": value, "extra_body": {"metadata": {"task": "test"}}}
    assert describe_reasoning_mode("openai:gpt-5.6-sol", settings).state == state
    assert apply_reasoning_mode("openai:gpt-5.6-sol", settings, "pro")["openai_reasoning_mode"] == "pro"


def test_submit_shares_typed_controls_without_changing_flat_wire_shape():
    request = SubmitRequest(parts=("hello",), thinking="high", fast=False, reasoning_mode="standard")
    assert request.controls() == ModelControlSelection(thinking="high", fast=False, reasoning_mode="standard")
    assert request.model_dump()["reasoning_mode"] == "standard"


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["standard", "pro"])
async def test_native_responses_request_serializes_mode_effort_summary_and_tier(mode):
    import json
    from typing import cast

    import httpx2 as httpx
    from openai import AsyncOpenAI
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai.settings import ModelSettings

    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "model": "gpt-5.6-sol",
                "status": "completed",
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "Done", "annotations": []}],
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        )

    settings = apply_model_controls(
        "openai-responses:gpt-5.6-sol",
        {"openai_reasoning_summary": "detailed"},
        ModelControlSelection(thinking="high", fast=True, reasoning_mode=mode),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = OpenAIProvider(openai_client=AsyncOpenAI(api_key="fixture", http_client=http))
        model = OpenAIResponsesModel("gpt-5.6-sol", provider=provider)
        result = await Agent(model, model_settings=cast(ModelSettings, settings)).run("Hello")
    assert result.output == "Done"
    assert requests[0]["reasoning"]["mode"] == mode
    assert requests[0]["reasoning"]["effort"] == "high"
    assert requests[0]["reasoning"]["summary"] == "detailed"
    assert requests[0]["service_tier"] == "priority"
