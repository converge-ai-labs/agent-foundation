"""Authored token prices retain unknowns and use whole-request cliff tiers."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from a13n_harness.pricing import ModelCostInput
from a13n_harness.token_pricing import TokenPricing, TokenPricingCapability
from pydantic import ValidationError
from pydantic_ai.usage import RequestUsage


def pricing(input_price="1", output_price="2"):
    return TokenPricing.model_validate(
        {
            "tiers": [
                {"rates": {"input": input_price, "output": output_price, "cache_read": "0"}},
                {"above": 200000, "rates": {"input": "10", "output": "20", "cache_read": "1"}},
            ]
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


def test_threshold_is_strict_and_prices_the_whole_request():
    policy = TokenPricingCapability({"root": pricing()})
    assert policy.quote(value(input_tokens=200000)).cost_usd == Decimal("0.2")
    assert policy.quote(value(input_tokens=200001)).cost_usd == Decimal("2.00001")


def test_cached_input_is_inclusive_and_zero_is_free():
    policy = TokenPricingCapability({"root": pricing()})
    assert policy.quote(value(input_tokens=100000, cache_read_tokens=50000)).cost_usd == Decimal("0.05")
    assert policy.quote(value(input_tokens=100000, cache_write_tokens=1)) is None
    assert policy.quote(value(input_tokens=1, cache_read_tokens=2)) is None
    assert policy.quote(value(input_tokens=1, input_audio_tokens=1)) is None


def test_shared_policy_selects_child_prices_by_selection_not_response_alias():
    policy = TokenPricingCapability({"root": pricing(), "child": pricing("9")})
    assert policy.quote(value("root", input_tokens=100000)).cost_usd == Decimal("0.1")
    assert policy.quote(value("child", input_tokens=100000)).cost_usd == Decimal("0.9")
    assert policy.quote(value("unknown", input_tokens=100000)) is None
    assert policy.quote(value(None, input_tokens=100000)) is None


@pytest.mark.parametrize(
    "tiers",
    [
        [],
        [{"above": 1, "rates": {}}],
        [{"rates": {}}, {"above": 20, "rates": {}}, {"above": 10, "rates": {}}],
        [{"rates": {}}, {"above": 10, "rates": {}}, {"above": 10, "rates": {}}],
        [{"rates": {"input": "-1"}}],
        [{"rates": {"input": "NaN"}}],
    ],
)
def test_invalid_tables_are_rejected(tiers):
    with pytest.raises(ValidationError):
        TokenPricing.model_validate({"tiers": tiers})


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

    policy = TokenPricingCapability({"logical:root": pricing()})
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
