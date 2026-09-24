"""Selected model IDs choose complete pricing entries, independent of the response model name."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from a13n_harness.pricing import ModelCostInput, ModelPricingEntry
from a13n_harness.token_pricing import TokenPricingCapability
from pydantic_ai.usage import RequestUsage


def entry(input_price="1", output_price="2"):
    return ModelPricingEntry.model_validate(
        {
            "provider": "openai",
            "model": "gpt-test",
            "rules": [
                {
                    "rule_id": "standard",
                    "prices": [
                        {"price_key": "input_mtok", "price": input_price},
                        {"price_key": "output_mtok", "price": output_price},
                    ],
                }
            ],
            "source": "custom",
            "source_revision": "2026-09-01",
        }
    )


def value(model="root", **usage):
    now = datetime.now(UTC)
    return ModelCostInput(
        selected_model_id=model,
        model_name="gateway-alias",
        provider_name="openai",
        provider_url=None,
        request_started_at=now,
        response_timestamp=now,
        usage=RequestUsage(**usage),
    )


def test_shared_policy_selects_child_prices_by_selection_not_response_alias():
    policy = TokenPricingCapability({"root": entry(), "child": entry("9")})
    assert policy.quote(value("root", input_tokens=100000)).cost_usd == Decimal("0.1")
    assert policy.quote(value("child", input_tokens=100000)).cost_usd == Decimal("0.9")
    assert policy.quote(value("unknown", input_tokens=100000)) is None
    assert policy.quote(value(None, input_tokens=100000)) is None


def test_a_complete_entry_prices_the_selected_model_whatever_it_names():
    compatible = ModelPricingEntry.model_validate(
        {
            "provider": "minimax",
            "model": "MiniMax-M3",
            "rules": [
                {
                    "rule_id": "standard",
                    "prices": [
                        {"price_key": "input_mtok", "price": "1", "tiers": [{"start": 200000, "price": "2"}]},
                        {"price_key": "input_audio_mtok", "price": "4"},
                    ],
                }
            ],
            "source": "models.dev",
            "source_revision": "2026-05-01",
        }
    )
    policy = TokenPricingCapability({"root": entry(), "compatible": compatible})
    quote = policy.quote(value("compatible", input_tokens=300000, input_audio_tokens=100000))
    # A tier prices the complete input, and audio input has its own price.
    assert quote is not None and (quote.cost_usd, quote.source, quote.rule_id) == (Decimal("0.8"), "custom", "standard")
    assert quote.pricing_revision == policy.revision != TokenPricingCapability({"root": entry()}).revision


@pytest.mark.anyio
async def test_resolver_selection_reaches_usage_valuation():
    from a13n_harness import HarnessBuilder, RunBindings
    from a13n_harness.usage import ModelUsageRecord
    from pydantic_ai.agent.spec import AgentSpec
    from pydantic_ai.models.function import FunctionModel

    async def stream(messages, info):
        yield "done"

    async def resolve(context, model_id):
        assert model_id == "logical:root"
        return FunctionModel(stream_function=stream, model_name="different-response-name")

    policy = TokenPricingCapability({"logical:root": entry()})
    executable = HarnessBuilder().build(
        AgentSpec(model="logical:root"),
        output_type=str,
        capabilities=(policy,),
    )
    result = await executable.run("hello", bindings=RunBindings.embedded(model_resolver=resolve))
    assert result.output_or_raise() == "done"
    record = result.usage_records[0]
    assert isinstance(record, ModelUsageRecord)
    assert record.pricing_status == "applied"
    assert record.pricing_revision == policy.revision
