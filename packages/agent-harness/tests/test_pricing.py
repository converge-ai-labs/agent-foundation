from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from a13n_harness import (
    AbstractModelCostCapability,
    CatalogModelCostCapability,
    DefinitionError,
    HarnessBuilder,
    ModelCostInput,
    ModelCostQuote,
    ModelPricingEntry,
    ModelUsageRecord,
    NoModelCostCapability,
    RunBindings,
    get_default_pricing_catalog,
    get_official_model_catalog,
)
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

    assert set(catalog) == {
        "anthropic:claude-opus-5",
        "anthropic:claude-sonnet-5",
        "deepseek:deepseek-v4-flash",
        "deepseek:deepseek-v4-pro",
        "google-gla:gemini-3.5-flash",
        "google-gla:gemini-3.5-flash-lite",
        "google-gla:gemini-3.6-flash",
        "openai:gpt-5.5",
    }
    assert catalog["openai:gpt-5.5"].configuration.context_window == 1_050_000
    assert all(entry.key.count(":") == 1 for entry in catalog.entries)


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
    assert current_peak is not None and current_peak.cost_usd == Decimal("1.760")
    assert current_peak.rule_id == "weekday-peak-1"
    assert current_off_peak is not None and current_off_peak.cost_usd == Decimal("0.880")
    assert weekend is not None and weekend.cost_usd == Decimal("0.880")


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

    result = await executable.run("go", bindings=RunBindings.local())

    record = result.usage_records[0]
    assert isinstance(record, ModelUsageRecord)
    assert record.pricing_status == "disabled"
    assert record.pricing_revision == "disabled"
    assert record.cost_source == "unknown"
