"""Served service tiers select per-response prices before native accumulation."""

import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import httpx2
import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.pricing import (
    CatalogModelCostCapability,
    ModelCostInput,
    ModelPricingEntry,
    get_default_pricing_catalog,
)
from a13n_harness.token_pricing import TokenPricingCapability
from a13n_harness.usage import ModelUsageRecord, _response_service_tier
from pydantic import ValidationError
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models import CompletedStreamedResponse, ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RequestUsage

NOW = datetime(2026, 9, 26, tzinfo=UTC)


def cost_input(tier=None, **usage):
    return ModelCostInput(
        model_name="gpt-5.5",
        provider_name="openai",
        provider_url=None,
        request_started_at=NOW,
        response_timestamp=NOW,
        usage=RequestUsage(**usage),
        service_tier=tier,
        selected_model_id="selected",
    )


def tier_entry():
    return ModelPricingEntry.model_validate(
        {
            "provider": "openai",
            "model": "test",
            "source": "custom",
            "source_revision": "v1",
            "rules": [
                {"rule_id": "base", "prices": [{"price_key": "input_mtok", "price": "10"}]},
                {"rule_id": "flex", "service_tier": "flex", "prices": [{"price_key": "input_mtok", "price": "5"}]},
                {
                    "rule_id": "flex-new",
                    "service_tier": "flex",
                    "constraint": {"kind": "start_date", "start_date": "2027-01-01"},
                    "prices": [{"price_key": "input_mtok", "price": "6"}],
                },
                {
                    "rule_id": "priority",
                    "service_tier": "priority",
                    "max_input_tokens": 272000,
                    "prices": [{"price_key": "input_mtok", "price": "20"}],
                },
                # An untiered later rule must not mask a tier-specific rule.
                {
                    "rule_id": "base-new",
                    "constraint": {"kind": "start_date", "start_date": "2026-09-01"},
                    "prices": [{"price_key": "input_mtok", "price": "12"}],
                },
            ],
        }
    )


@pytest.mark.parametrize(
    "tier,rule",
    [
        (None, "base-new"),
        ("default", "base-new"),
        ("standard", "base-new"),
        ("on_demand", "base-new"),
        ("flex", "flex"),
        ("priority", "priority"),
    ],
)
def test_served_tier_and_time_select_independent_rules(tier, rule):
    entry = tier_entry()
    assert entry.select_rule(NOW, service_tier=tier).rule_id == rule
    assert entry.select_rule(datetime(2027, 1, 1, tzinfo=UTC), service_tier="flex").rule_id == "flex-new"
    assert entry.select_rule(NOW, service_tier="auto") is None
    assert entry.select_rule(NOW, service_tier="enterprise") is None


def test_selected_model_policy_uses_tier_without_changing_response_identity():
    policy = TokenPricingCapability({"selected": tier_entry()})
    assert policy.quote(cost_input("flex", input_tokens=1_000_000)).cost_usd == Decimal("5")
    assert policy.quote(cost_input("priority", input_tokens=272001)) is None
    assert policy.quote(cost_input("priority", input_tokens=272000)).cost_usd == Decimal("5.44")
    assert policy.quote(cost_input("unknown", input_tokens=1)) is None
    assert policy.quote(replace(cost_input("flex"), selected_model_id="missing")) is None


def test_legacy_pricing_roundtrip_and_tier_only_entry_validation():
    entry = tier_entry()
    payload = entry.model_dump(mode="json")
    assert ModelPricingEntry.model_validate(payload) == entry
    payload["rules"] = [payload["rules"][1]]
    with pytest.raises(ValidationError, match="untiered always rule"):
        ModelPricingEntry.model_validate(payload)
    payload["rules"] = [{"rule_id": "legacy", "prices": [{"price_key": "input_mtok", "price": "1"}]}]
    legacy = ModelPricingEntry.model_validate(payload)
    assert legacy.quote(cost_input(input_tokens=1_000_000), source="custom", revision="v1").cost_usd == 1
    assert legacy.quote(cost_input("flex", input_tokens=1_000_000), source="custom", revision="v1") is None


@pytest.mark.parametrize("value", ["", 1, {}, "x" * 65, "priority\nsecret", "秘密", "PRIORITY"])
def test_invalid_response_tier_does_not_become_a_standard_price(value):
    with pytest.raises(ValueError, match="bounded identifier"):
        _response_service_tier(ModelResponse([], provider_details={"service_tier": value}))


def test_response_tier_extracts_no_other_provider_payload():
    assert _response_service_tier(ModelResponse([])) is None
    assert (
        _response_service_tier(ModelResponse([], provider_details={"service_tier": "flex", "secret": "not copied"}))
        == "flex"
    )


def response_payload(api, tier):
    if api == "chat":
        payload = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1780000000,
            "model": "gpt-5.5",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 100,
                "total_tokens": 1100,
                "prompt_tokens_details": {"cached_tokens": 100},
            },
        }
    else:
        payload = {
            "id": "resp-test",
            "object": "response",
            "created_at": 1780000000,
            "model": "gpt-5.5",
            "status": "completed",
            "output": [
                {
                    "id": "msg-test",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "done", "annotations": []}],
                }
            ],
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 100,
                "total_tokens": 1100,
                "input_tokens_details": {"cached_tokens": 100},
            },
        }
    if tier is not None:
        payload["service_tier"] = tier
    return payload


def response_stream(api, tier):
    payload = response_payload(api, tier)
    if api == "chat":
        events = [
            {
                **payload,
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": "done"}, "finish_reason": None}],
                "usage": None,
            },
            {
                **payload,
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": None,
            },
            # The usage-only chunk has no choices but carries the actual tier.
            {**payload, "object": "chat.completion.chunk", "choices": []},
        ]
    else:
        events = [
            {
                "type": "response.created",
                "sequence_number": 0,
                "response": {
                    **payload,
                    "service_tier": "priority",
                    "status": "in_progress",
                    "output": [],
                    "usage": None,
                },
            },
            {
                "type": "response.output_item.added",
                "sequence_number": 1,
                "output_index": 0,
                "item": {**payload["output"][0], "content": [], "status": "in_progress"},
            },
            {
                "type": "response.content_part.added",
                "sequence_number": 2,
                "item_id": "msg-test",
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            },
            {
                "type": "response.output_text.delta",
                "sequence_number": 3,
                "item_id": "msg-test",
                "output_index": 0,
                "content_index": 0,
                "delta": "done",
            },
            {"type": "response.completed", "sequence_number": 4, "response": payload},
        ]
    return "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"


@pytest.mark.anyio
@pytest.mark.parametrize("api", ["chat", "responses"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "tier,expected",
    [(None, "0.00755"), ("default", "0.00755"), ("flex", "0.003775"), ("priority", "0.018875"), ("fast", "0.018875")],
)
async def test_openai_served_tier_reaches_cost_not_requested_priority(api, streaming, tier, expected):
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if body.get("stream"):
            return httpx2.Response(
                200, headers={"content-type": "text/event-stream"}, content=response_stream(api, tier)
            )
        return httpx2.Response(200, json=response_payload(api, tier))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        provider = OpenAIProvider(api_key="test", http_client=client)
        model = (OpenAIChatModel if api == "chat" else OpenAIResponsesModel)("gpt-5.5", provider=provider)
        if not streaming:
            response = await model.request([], {"service_tier": "priority"}, ModelRequestParameters())
            assert _response_service_tier(response) == tier
            value = replace(cost_input(tier), usage=response.usage)
            quote = get_default_pricing_catalog()["openai:gpt-5.5"].quote(value, source="catalog", revision="test")
            assert quote.cost_usd == Decimal(expected)
        else:
            executable = HarnessBuilder().build(
                AgentSpec(model_settings={"service_tier": "priority"}), output_type=str, model=model
            )
            result = await executable.run("go", bindings=RunBindings.embedded())
            assert result.output_or_raise() == "done"
            assert result.usage.cost == Decimal(expected)
            record = result.usage_records[0]
            assert isinstance(record, ModelUsageRecord)
            assert record.pricing_status == "applied"
            assert record.request_usage.cost == result.usage.cost
    assert len(requests) == 1 and requests[0]["service_tier"] == "priority"


@pytest.mark.parametrize(
    "model,tier,input_tokens,cached,output,expected",
    [
        ("openai:gpt-5.5", "flex", 272000, 0, 100, "0.6815"),
        ("openai:gpt-5.5", "flex", 272001, 0, 100, "1.362255"),
        ("openai:gpt-5.5", "priority", 272001, 0, 100, None),
        ("openai:gpt-5.5-pro", "flex", 272001, 0, 100, None),
        ("openai:gpt-5.4", "flex", 1000000, 1000000, 0, "0.25"),
        ("openai:o4-mini", "flex", 1000, 1000, 0, "0.000138"),
        ("openai:gpt-4.1", "priority", 1000000, 0, 0, "3.5"),
        ("openai:gpt-4.1", "flex", 1000, 0, 0, None),
        ("google:gemini-2.5-pro", "flex", 200000, 0, 100, "0.1255"),
        ("google:gemini-2.5-pro", "flex", 200001, 0, 100, "0.25075125"),
        ("google:gemini-2.5-flash", "flex", 1000000, 1000000, 0, "0.03"),
        ("google:gemini-3.5-flash", "flex", 1000000, 1000000, 0, "0.08"),
        ("google:gemini-3.5-flash-lite", "priority", 1000000, 1000000, 0, "0.05"),
    ],
)
def test_literal_vendor_prices_and_context_boundaries(model, tier, input_tokens, cached, output, expected):
    entry = get_default_pricing_catalog()[model]
    quote = entry.quote(
        cost_input(tier, input_tokens=input_tokens, cache_read_tokens=cached, output_tokens=output),
        source="catalog",
        revision="test",
    )
    if expected is None:
        assert quote is None
    else:
        assert quote.cost_usd == Decimal(expected)


@pytest.mark.parametrize("model", ["gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash"])
@pytest.mark.parametrize("tier,price", [(None, "0.75"), ("flex", "0.375"), ("priority", "1.35")])
def test_gemini_promotional_dates_are_independent_of_tiers(model, tier, price):
    entry = get_default_pricing_catalog()[f"google:{model}"]
    value = cost_input(tier, input_tokens=1000000)
    assert entry.quote(value, source="catalog", revision="test").cost_usd == Decimal(price)
    later = replace(value, request_started_at=datetime(2027, 1, 1, tzinfo=UTC))
    assert entry.quote(later, source="catalog", revision="test").cost_usd == Decimal(price) * 2


def test_gemini_flex_audio_cache_keeps_its_published_rate():
    entry = get_default_pricing_catalog()["google:gemini-2.5-flash"]
    value = cost_input(
        "flex",
        input_tokens=1000,
        cache_read_tokens=100,
        input_audio_tokens=1000,
        cache_audio_read_tokens=100,
    )
    assert entry.quote(value, source="catalog", revision="test").cost_usd == Decimal("0.00046")


@pytest.mark.parametrize("traffic", ["ON_DEMAND", "ON_DEMAND_FLEX", "ON_DEMAND_PRIORITY", "PROVISIONED_THROUGHPUT"])
def test_vertex_actual_traffic_never_borrows_developer_api_tier_prices(traffic):
    response = ModelResponse([], provider_name="google-vertex", provider_details={"traffic_type": traffic})
    tier = _response_service_tier(response)
    assert tier == traffic.lower()
    value = replace(cost_input(tier, input_tokens=1000), provider_name="google-vertex", model_name="gemini-2.5-flash")
    assert CatalogModelCostCapability(catalog=get_default_pricing_catalog()).quote(value) is None


@pytest.mark.anyio
@pytest.mark.parametrize("tier,status", [("enterprise", "declined"), ("auto", "declined"), (42, "failed")])
async def test_unpriced_or_invalid_tier_preserves_upstream_cost_and_reports_coverage(tier, status, monkeypatch):
    def respond(messages, info):
        return ModelResponse(
            [TextPart("done")],
            model_name="gpt-5.5",
            provider_name="openai",
            provider_details={"service_tier": tier},
            usage=RequestUsage(input_tokens=1000, output_tokens=100, cost=Decimal("0.123")),
        )

    @asynccontextmanager
    async def request_stream(messages, model_settings, model_request_parameters, run_context=None):
        yield CompletedStreamedResponse(
            respond(messages, None),
            model_request_parameters=model_request_parameters,
            replay_events=True,
        )

    model = FunctionModel(respond)
    monkeypatch.setattr(model, "request_stream", request_stream)
    executable = HarnessBuilder().build(AgentSpec(), output_type=str, model=model)
    result = await executable.run("go", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    assert result.usage.cost == Decimal("0.123")
    record = result.usage_records[0]
    assert record.pricing_status == status
    assert record.cost_source == "provider_or_genai_prices"


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("tier,expected", [("standard", "0.000523"), ("flex", "0.000263"), ("priority", "0.0009414")])
async def test_gemini_served_header_selects_actual_tier(streaming, tier, expected):
    payload = {
        "candidates": [{"content": {"parts": [{"text": "done"}], "role": "model"}, "finishReason": "STOP"}],
        "usageMetadata": {
            "promptTokenCount": 1000,
            "cachedContentTokenCount": 100,
            "candidatesTokenCount": 100,
            "totalTokenCount": 1100,
        },
        "modelVersion": "gemini-2.5-flash",
        "responseId": "response-test",
    }

    def handle(request):
        headers = {"x-gemini-service-tier": tier.upper()}
        if streaming:
            return httpx2.Response(
                200,
                headers={**headers, "content-type": "text/event-stream"},
                content="data: " + json.dumps(payload) + "\n\n",
            )
        return httpx2.Response(200, headers=headers, json=payload)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        model = GoogleModel("gemini-2.5-flash", provider=GoogleProvider(api_key="test", http_client=client))
        if streaming:
            executable = HarnessBuilder().build(AgentSpec(), output_type=str, model=model)
            result = await executable.run("go", bindings=RunBindings.embedded())
            assert result.output_or_raise() == "done"
            assert result.usage.cost == Decimal(expected)
        else:
            response = await model.request([ModelRequest(parts=[UserPromptPart("go")])], None, ModelRequestParameters())
            assert _response_service_tier(response) == tier
            value = replace(
                cost_input(tier),
                usage=response.usage,
                provider_name=response.provider_name,
                model_name=response.model_name,
            )
            quote = CatalogModelCostCapability(catalog=get_default_pricing_catalog()).quote(value)
            assert quote.cost_usd == Decimal(expected)
