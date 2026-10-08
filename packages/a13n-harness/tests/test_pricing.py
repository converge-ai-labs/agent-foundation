from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from a13n_harness import (
    DefinitionError,
    HarnessBuilder,
    RunBindings,
)
from a13n_harness.model_catalog import get_official_model_catalog
from a13n_harness.pricing import (
    AbstractModelCostCapability,
    CatalogModelCostCapability,
    ModelCostInput,
    ModelCostQuote,
    ModelPricingEntry,
    NoModelCostCapability,
    get_default_pricing_catalog,
)
from a13n_harness.usage import ModelUsageRecord
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage


def _cost_input(
    model: str,
    provider: str,
    timestamp: datetime,
    *,
    input_tokens: int = 1_000_000,
    output_tokens: int = 1_000_000,
) -> ModelCostInput:
    return ModelCostInput(
        model_name=model,
        provider_name=provider,
        provider_url=None,
        request_started_at=timestamp,
        response_timestamp=timestamp,
        usage=RequestUsage(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _model() -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    return FunctionModel(stream_function=stream)


def test_official_model_catalog_contains_only_provider_qualified_direct_models() -> None:
    catalog = get_official_model_catalog()

    assert set(catalog) >= {
        "anthropic:claude-fable-5-1",
        "anthropic:claude-opus-5",
        "anthropic:claude-sonnet-5",
        "deepseek:deepseek-v4-flash",
        "deepseek:deepseek-v4-pro",
        "google-gla:gemini-3.5-flash",
        "google-gla:gemini-3.5-flash-lite",
        "google-gla:gemini-3.6-flash",
        "openai:gpt-5.5",
    }
    assert catalog["openai:gpt-5.5"].characteristics.context_window_tokens == 1_050_000
    assert all(entry.key.count(":") == 1 for entry in catalog.entries)
    assert {entry.key.partition(":")[0] for entry in catalog.entries} <= {
        "alibaba",
        "alibaba-cn",
        "anthropic",
        "cerebras",
        "deepseek",
        "fireworks",
        "groq",
        "minimax",
        "mistral",
        "sambanova",
        "together",
        "google-gla",
        "grok",
        "moonshotai",
        "openai",
        "zai",
    }
    assert catalog["openai:gpt-6-astra"].characteristics.capabilities == {"image_understanding"}
    assert catalog["google-gla:gemini-2.5-pro"].characteristics.capabilities == {
        "image_understanding",
        "audio_understanding",
        "video_understanding",
    }
    text_only = catalog["zai:glm-4.7"].characteristics
    assert not text_only.capabilities and "capabilities" in text_only.model_fields_set
    text_only = catalog["deepseek:deepseek-v4-pro"].characteristics
    assert not text_only.capabilities and "capabilities" in text_only.model_fields_set


def test_default_pricing_catalog_exports_genai_snapshot_plus_harness_overlay() -> None:
    catalog = get_default_pricing_catalog()
    exported = catalog.model_dump(mode="json")

    assert len(catalog) > 1_000
    assert catalog["openai:gpt-5.5"].source == "harness_overlay"
    assert catalog["deepseek:deepseek-chat"].source == "genai_prices"
    assert exported["revision"] == catalog.revision
    assert set(exported["entries"]) == set(catalog)


def test_catalog_preserves_genai_and_overlay_time_window_pricing() -> None:
    capability = CatalogModelCostCapability()

    legacy_peak = capability.quote(_cost_input("deepseek-chat", "deepseek", datetime(2026, 8, 24, 2, tzinfo=UTC)))
    legacy_off_peak = capability.quote(_cost_input("deepseek-chat", "deepseek", datetime(2026, 8, 24, 20, tzinfo=UTC)))
    current_peak = capability.quote(_cost_input("deepseek-v4-flash", "deepseek", datetime(2026, 8, 24, 2, tzinfo=UTC)))
    current_off_peak = capability.quote(
        _cost_input("deepseek-v4-flash", "deepseek", datetime(2026, 8, 24, 5, tzinfo=UTC))
    )
    weekend = capability.quote(_cost_input("deepseek-v4-flash", "deepseek", datetime(2026, 8, 23, 2, tzinfo=UTC)))

    assert legacy_peak is not None and legacy_peak.cost_usd == Decimal("1.37")
    assert legacy_off_peak is not None and legacy_off_peak.cost_usd == Decimal("0.685")
    assert current_peak is not None and current_peak.cost_usd == Decimal("1.500")
    assert current_peak.rule_id == "weekday-peak-1"
    assert current_off_peak is not None and current_off_peak.cost_usd == Decimal("0.750")
    assert weekend is not None and weekend.cost_usd == Decimal("0.750")


def test_pricing_updates_replace_one_complete_entry_without_mutating_default() -> None:
    default = get_default_pricing_catalog()
    original = default["openai:gpt-5.5"]
    replacement = ModelPricingEntry(
        provider="openai",
        model="gpt-5.5",
        context_window=original.context_window,
        rules=original.rules,
        source="host",
        source_revision="host-price-v2",
        source_url=original.source_url,
    )

    updated = default.with_updates({replacement.key: replacement})

    assert updated[replacement.key] == replacement
    assert updated.revision != default.revision
    assert default[replacement.key] == original
    assert updated["anthropic:claude-sonnet-5"] == default["anthropic:claude-sonnet-5"]


class _FixedCostCapability(AbstractModelCostCapability):
    @property
    def revision(self) -> str:
        return "fixed-v1"

    def quote(self, value: ModelCostInput) -> ModelCostQuote:
        del value
        return ModelCostQuote(
            cost_usd=Decimal("0.25"),
            source="custom",
            pricing_revision=self.revision,
            rule_id="fixed",
        )


def _model_cost_leaves(executable) -> tuple[AbstractModelCostCapability, ...]:
    leaves = []
    executable._agent.root_capability.apply(leaves.append)
    return tuple(item for item in leaves if isinstance(item, AbstractModelCostCapability))


def test_builder_inserts_default_and_one_custom_atomically_replaces_it() -> None:
    default = HarnessBuilder().build(AgentSpec(), output_type=str, model=_model())
    custom = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_model(),
        capabilities=(_FixedCostCapability(),),
    )

    assert len(_model_cost_leaves(default)) == 1
    assert isinstance(_model_cost_leaves(default)[0], CatalogModelCostCapability)
    assert len(_model_cost_leaves(custom)) == 1
    assert isinstance(_model_cost_leaves(custom)[0], _FixedCostCapability)


def test_builder_rejects_multiple_model_cost_capabilities() -> None:
    with pytest.raises(DefinitionError, match="at most one model-cost"):
        HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=_model(),
            capabilities=(_FixedCostCapability(), NoModelCostCapability()),
        )


@pytest.mark.anyio
async def test_no_model_cost_capability_explicitly_disables_harness_valuation() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_model(),
        capabilities=(NoModelCostCapability(),),
    )

    result = await executable.run("go", bindings=RunBindings.embedded())

    record = result.usage_records[0]
    assert isinstance(record, ModelUsageRecord)
    assert record.pricing_status == "disabled"
    assert record.pricing_revision == "disabled"
    assert record.cost_source == "unknown"


def test_official_metadata_reuses_bundled_context_and_supplements_missing_models() -> None:
    from importlib.resources import files

    import yaml

    official = get_official_model_catalog()
    pricing = get_default_pricing_catalog()
    raw = yaml.safe_load(files("a13n_harness").joinpath("data/official-models.yaml").read_text())
    for model, value in raw["models"].items():
        provider, _, name = model.partition(":")
        price_provider = {"grok": "x-ai", "google-gla": "google"}.get(provider, provider)
        price_key = f"{price_provider}:{name}"
        price = pricing.get(price_key)
        facts = value.get("characteristics", {})
        if facts and price is not None and price.context_window is not None:
            expected = facts.get("context_window_tokens", price.context_window)
            assert official[model].characteristics.context_window_tokens == expected
        assert "context_window" not in value.get("pricing", {})
    for name in ("gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna"):
        facts = official[f"openai:{name}"].characteristics
        assert facts.context_window_tokens == 1050000
        assert facts.capabilities == {"image_understanding"}


@pytest.mark.parametrize(
    "context,expected", [("", 123456), ("context_window_tokens: null", None), ("context_window_tokens: 456789", 456789)]
)
def test_official_context_merge_preserves_explicit_supplements_and_missing_media(
    monkeypatch, context, expected
) -> None:
    from a13n_harness import model_catalog
    from a13n_harness.pricing import PricingCatalog

    price = get_default_pricing_catalog()["openai:gpt-5.5"].model_copy(
        update={"model": "fixture", "context_window": 123456}
    )
    from a13n_harness._official_data import parse_official_data

    data = parse_official_data(
        "schema_version: 2\nmodels:\n  openai:fixture:\n"
        f"    characteristics: {{{context}}}\n"
        "    source_url: https://example.com/model\n"
    )
    monkeypatch.setattr(model_catalog, "current_official_data", lambda: data)
    monkeypatch.setattr(model_catalog, "_bundled_upstream_catalog", lambda: PricingCatalog({price.key: price}))
    facts = get_official_model_catalog()["openai:fixture"].characteristics
    assert facts.context_window_tokens == expected
    assert "capabilities" not in facts.model_fields_set


@pytest.mark.parametrize(
    "key,context,input_price,output_price",
    [
        ("anthropic:claude-sonnet-5-5", 1000000, "2", "10"),
        ("anthropic:claude-haiku-5-5", 1000000, "0.1", "0.5"),
        ("moonshotai:kimi-k3", 1048576, "3", "15"),
        ("x-ai:grok-4.7", 500000, "2", "6"),
        ("x-ai:grok-4.20-0309-reasoning", 1000000, "1.25", "2.5"),
        ("mistral:mistral-large-4", 1000000, "0.68", "2.09"),
        ("fireworks:accounts/fireworks/models/ember-1", 1040000, "3", "15"),
        ("together:moonshotai/Kimi-K3", 1048576, "3", "15"),
        ("together:deepseek-ai/DeepSeek-V4.1-Flash", 1000000, "0.3", "1.2"),
        ("minimax:MiniMax-M3", 1000000, "0.3", "1.2"),
    ],
)
def test_reviewed_provider_prices_and_context(key, context, input_price, output_price):
    entry = get_default_pricing_catalog()[key]
    assert entry.context_window == context
    parts = {part.price_key: part.price for part in entry.rules[0].prices}
    assert parts["input_mtok"] == Decimal(input_price)
    assert parts["output_mtok"] == Decimal(output_price)


@pytest.mark.parametrize("tokens,rate", [(199999, "2"), (200000, "4")])
def test_grok_long_context_includes_threshold(tokens, rate):
    quote = CatalogModelCostCapability().quote(
        _cost_input("grok-4.7", "x-ai", datetime(2026, 10, 8, tzinfo=UTC), input_tokens=tokens, output_tokens=0)
    )
    assert quote is not None
    assert quote.cost_usd == Decimal(tokens) * Decimal(rate) / 1_000_000
