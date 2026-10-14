"""Unused modality intersections must not prevent valuation of observed token usage."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from a13n_harness.pricing import ModelCostInput, ModelPricingEntry
from pydantic_ai.usage import RequestUsage


def entry(**prices):
    return ModelPricingEntry.model_validate(
        {
            "provider": "google-vertex",
            "model": "gemini-test",
            "source": "models.dev",
            "source_revision": "test",
            "rules": [
                {
                    "rule_id": "standard",
                    "prices": [
                        {"price_key": key, "price": rate} for key, rate in {"input_mtok": "2", **prices}.items()
                    ],
                }
            ],
        }
    )


def quote(pricing, **usage):
    now = datetime(2026, 10, 14, tzinfo=UTC)
    value = ModelCostInput(
        model_name="gateway-alias",
        provider_name="google-cloud",
        provider_url=None,
        request_started_at=now,
        response_timestamp=now,
        usage=RequestUsage(**usage),
    )
    return pricing.quote(value, source="custom", revision="test")


@pytest.mark.parametrize("modality", ["text", "audio", "image", "video"])
@pytest.mark.parametrize("cache", ["read", "write"])
def test_unused_media_prices_do_not_block_cached_text_requests(modality, cache):
    pricing = entry(**{f"input_{modality}_mtok": "4", f"cache_{cache}_mtok": "0.1", "output_mtok": "10"})
    original = pricing.model_dump_json()
    result = quote(pricing, input_tokens=1000, output_tokens=100, **{f"cache_{cache}_tokens": 800})
    assert result is not None and result.cost_usd == Decimal("0.00148")
    assert pricing.model_dump_json() == original


@pytest.mark.parametrize("modality", ["text", "audio", "image", "video"])
@pytest.mark.parametrize("cache", ["read", "write"])
def test_disjoint_cached_and_media_tokens_need_no_intersection_tariff(modality, cache):
    pricing = entry(**{f"input_{modality}_mtok": "4", f"cache_{cache}_mtok": "0.1", "output_mtok": "10"})
    result = quote(
        pricing,
        input_tokens=1000,
        output_tokens=100,
        **{f"input_{modality}_tokens": 100, f"cache_{cache}_tokens": 400, f"cache_{modality}_{cache}_tokens": 0},
    )
    assert result is not None and result.cost_usd == Decimal("0.00244")


@pytest.mark.parametrize("modality", ["text", "audio", "image", "video"])
@pytest.mark.parametrize("cached", [None, 100])
def test_positive_or_ambiguous_intersections_without_prices_decline(modality, cached):
    pricing = entry(**{f"input_{modality}_mtok": "4", "cache_read_mtok": "0.1"})
    assert (
        quote(
            pricing,
            input_tokens=1000,
            cache_read_tokens=400,
            **{f"input_{modality}_tokens": 200, f"cache_{modality}_read_tokens": cached},
        )
        is None
    )


@pytest.mark.parametrize("modality", ["text", "audio", "image", "video"])
def test_published_cached_media_prices_apply_without_double_counting(modality):
    pricing = entry(
        **{
            f"input_{modality}_mtok": "4",
            f"cache_{modality}_read_mtok": "0.5",
            "cache_read_mtok": "0.1",
            "output_mtok": "10",
        }
    )
    result = quote(
        pricing,
        input_tokens=1000,
        cache_read_tokens=400,
        output_tokens=100,
        **{f"input_{modality}_tokens": 200, f"cache_{modality}_read_tokens": 100},
    )
    assert result is not None and result.cost_usd == Decimal("0.00248")


@pytest.mark.parametrize("modality", ["text", "audio", "image", "video"])
def test_unused_cache_ttl_intersections_and_ancestors_are_completed(modality):
    pricing = entry(**{f"input_{modality}_mtok": "4", "cache_write_mtok": "3", "cache_write_1h_mtok": "5"})
    result = quote(pricing, input_tokens=1000, cache_write_tokens=400, cache_write_1h_tokens=400)
    assert result is not None and result.cost_usd == Decimal("0.0032")


@pytest.mark.parametrize("modality", ["text", "audio", "image", "video"])
def test_output_media_and_reasoning_have_the_same_intersection_rules(modality):
    pricing = entry(**{"output_mtok": "10", f"output_{modality}_mtok": "20", "output_reasoning_mtok": "15"})
    result = quote(
        pricing,
        output_tokens=1000,
        output_reasoning_tokens=200,
        **{f"output_{modality}_tokens": 100, f"output_{modality}_reasoning_tokens": 0},
    )
    assert result is not None and result.cost_usd == Decimal("0.012")
    assert quote(pricing, output_tokens=1000, output_reasoning_tokens=200, **{f"output_{modality}_tokens": 100}) is None


def test_context_tiers_still_use_the_complete_request_input():
    pricing = entry(input_audio_mtok="4", cache_read_mtok="0.1", output_mtok="10")
    payload = pricing.model_dump(mode="json")
    for component in payload["rules"][0]["prices"]:
        if component["price_key"] in {"input_mtok", "input_audio_mtok"}:
            component["tiers"] = [{"start": 200000, "price": str(Decimal(component["price"]) * 2)}]
    result = quote(
        ModelPricingEntry.model_validate(payload),
        input_tokens=1000000,
        input_audio_tokens=200000,
        cache_read_tokens=400000,
        cache_audio_read_tokens=0,
        output_tokens=100000,
    )
    assert result is not None and result.cost_usd == Decimal("4.24")


def test_inconsistent_inclusive_usage_is_still_rejected():
    with pytest.raises(ValueError, match="cannot exceed"):
        quote(entry(input_audio_mtok="4", cache_read_mtok="0.1"), input_tokens=10, input_audio_tokens=20)
