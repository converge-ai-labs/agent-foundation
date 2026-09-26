from __future__ import annotations

import threading
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx2
import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings, pricing
from a13n_harness.pricing import CatalogModelCostCapability, ModelCostInput, NoModelCostCapability
from a13n_harness.usage import ModelUsageRecord
from anyio import fail_after, sleep, to_thread
from genai_prices import UpdatePrices
from genai_prices.data_snapshot import DataSnapshot, get_snapshot, set_custom_snapshot
from genai_prices.types import ClauseEquals, ModelInfo, ModelPrice, Provider
from pydantic_ai import prices
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage


@pytest.fixture(autouse=True)
def isolated_prices() -> Iterator[None]:
    previous = get_snapshot()
    set_custom_snapshot(None)
    pricing.get_current_pricing_catalog()
    try:
        yield
    finally:
        set_custom_snapshot(None)
        pricing.get_current_pricing_catalog()
        set_custom_snapshot(previous)


def _snapshot(price: str = "1") -> DataSnapshot:
    return DataSnapshot(
        providers=[
            Provider(
                id="openai",
                name="OpenAI",
                api_pattern="https://api.openai.com/.*",
                models=[
                    ModelInfo(
                        id="gpt-5.5", match=ClauseEquals(equals="gpt-5.5"), prices=ModelPrice(input_mtok=Decimal(price))
                    )
                ],
            )
        ],
        from_auto_update=True,
    )


def _input() -> ModelCostInput:
    return ModelCostInput(
        model_name="gpt-5.5",
        provider_name="openai",
        provider_url=None,
        request_started_at=datetime.now(UTC),
        response_timestamp=datetime.now(UTC),
        usage=RequestUsage(input_tokens=1_000_000),
    )


def _capability(executable) -> CatalogModelCostCapability:
    leaves = []
    executable._agent.root_capability.apply(leaves.append)
    return next(item for item in leaves if isinstance(item, CatalogModelCostCapability))


def test_default_catalog_is_bundled_even_when_first_loaded_after_an_update() -> None:
    original = pricing.get_default_pricing_catalog()
    set_custom_snapshot(_snapshot())
    pricing.get_default_pricing_catalog.cache_clear()
    default = pricing.get_default_pricing_catalog()
    assert default.revision == original.revision
    assert default["openai:gpt-5.5"].source == "harness_overlay"
    assert pricing.get_current_pricing_catalog()["openai:gpt-5.5"].source == "genai_prices"


def test_refresh_replaces_packaged_prices_and_preserves_missing_models_and_aliases() -> None:
    bundled = pricing.get_default_pricing_catalog()
    set_custom_snapshot(_snapshot())
    current = pricing.get_current_pricing_catalog()
    assert current["openai:gpt-5.5"].rules[0].prices[0].price == Decimal(1)
    assert current["anthropic:claude-fable-5-1"] == bundled["anthropic:claude-fable-5-1"]
    assert current.resolve("deepseek-chat", provider="deepseek") == bundled.resolve(
        "deepseek-chat", provider="deepseek"
    )
    assert current.resolve("gpt-5.5", provider_url="https://api.openai.com/v1") == current["openai:gpt-5.5"]
    assert pricing.get_current_pricing_catalog() is current


def test_explicit_updates_win_and_snapshots_are_detached() -> None:
    source = _snapshot()
    set_custom_snapshot(source)
    current = pricing.get_current_pricing_catalog()
    override = pricing.get_default_pricing_catalog()["openai:gpt-5.5"]
    capability = CatalogModelCostCapability(pricing_updates={override.key: override})
    source.providers[0].models[0].prices = ModelPrice(input_mtok=Decimal(999))
    assert capability.catalog[override.key] == override
    assert current[override.key].rules[0].prices[0].price == Decimal(1)
    assert current.resolve("gpt-5.5", provider_url="https://api.openai.com/v1") == current[override.key]


def test_retrieval_time_does_not_change_revision_but_price_changes_do() -> None:
    source = _snapshot()
    set_custom_snapshot(source)
    first = pricing.get_current_pricing_catalog()
    later = deepcopy(source)
    later.timestamp += timedelta(hours=1)
    set_custom_snapshot(later)
    second = pricing.get_current_pricing_catalog()
    assert second.revision == first.revision
    assert second["openai:gpt-5.5"].source_revision == first["openai:gpt-5.5"].source_revision
    set_custom_snapshot(_snapshot("2"))
    assert pricing.get_current_pricing_catalog().revision != first.revision


@pytest.mark.parametrize("has_previous", [False, True])
def test_invalid_snapshot_retains_last_good_and_is_attempted_once(caplog, has_previous) -> None:
    if has_previous:
        set_custom_snapshot(_snapshot())
    previous = pricing.get_current_pricing_catalog()
    set_custom_snapshot(_snapshot("-1"))
    assert pricing.get_current_pricing_catalog() is previous
    assert pricing.get_current_pricing_catalog() is previous
    assert caplog.text.count("pricing_catalog_update_failed") == 1
    set_custom_snapshot(_snapshot("2"))
    assert pricing.get_current_pricing_catalog().revision != previous.revision


def test_empty_snapshot_does_not_replace_last_good_catalog() -> None:
    set_custom_snapshot(_snapshot())
    previous = pricing.get_current_pricing_catalog()
    set_custom_snapshot(DataSnapshot([], from_auto_update=True))
    assert pricing.get_current_pricing_catalog() is previous


def test_concurrent_readers_share_one_complete_catalog() -> None:
    set_custom_snapshot(_snapshot())
    with ThreadPoolExecutor(max_workers=8) as pool:
        catalogs = list(pool.map(lambda _: pricing.get_current_pricing_catalog(), range(16)))
    assert all(item is catalogs[0] for item in catalogs)


def test_new_builds_automatically_adopt_updates_but_existing_and_pinned_builds_do_not() -> None:
    set_custom_snapshot(_snapshot())
    first = HarnessBuilder().build(AgentSpec(model="test"), output_type=str)
    first_catalog = _capability(first).catalog
    set_custom_snapshot(_snapshot("2"))
    second = HarnessBuilder().build(AgentSpec(model="test"), output_type=str)
    pinned = HarnessBuilder().build(AgentSpec(model="test"), output_type=str, pricing_catalog=first_catalog)
    assert _capability(first).quote(_input()).cost_usd == Decimal(1)
    assert _capability(second).quote(_input()).cost_usd == Decimal(2)
    assert _capability(pinned).catalog is first_catalog
    assert _capability(pinned).revision == _capability(first).revision


def test_explicit_capability_still_wins_over_build_catalog() -> None:
    disabled = NoModelCostCapability()
    executable = HarnessBuilder().build(
        AgentSpec(model="test"),
        output_type=str,
        capabilities=(disabled,),
        pricing_catalog=pricing.get_default_pricing_catalog(),
    )
    leaves = []
    executable._agent.root_capability.apply(leaves.append)
    assert any(isinstance(item, NoModelCostCapability) for item in leaves)
    assert not any(isinstance(item, CatalogModelCostCapability) for item in leaves)


@pytest.mark.anyio
async def test_upstream_background_refresh_prices_next_execution_without_manual_cache_clear(monkeypatch) -> None:
    payload = [
        {
            "id": "function",
            "name": "Test provider",
            "model_match": {"equals": "priced-model"},
            "api_pattern": "https://example.invalid/.*",
            "models": [
                {
                    "id": "priced-model",
                    "match": {"equals": "priced-model"},
                    "prices": {"input_mtok": "1", "output_mtok": "1"},
                }
            ],
        }
    ]

    def download(url, **kwargs):
        return httpx2.Response(200, json=payload, request=httpx2.Request("GET", url))

    monkeypatch.setattr(httpx2, "get", download)
    monkeypatch.setattr(prices, "UpdatePrices", lambda: UpdatePrices(update_interval=0.01))

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "done"

    model = FunctionModel(stream_function=stream, model_name="priced-model")
    updater = prices.update_in_background()
    try:
        assert await to_thread.run_sync(updater.wait, 5)
        first = HarnessBuilder().build(AgentSpec(), output_type=str, model=model)
        initial = await first.run("go", bindings=RunBindings.embedded())
        payload[0]["models"][0]["prices"] = {"input_mtok": "2", "output_mtok": "2"}
        with fail_after(5):
            while True:
                current = await to_thread.run_sync(pricing.get_current_pricing_catalog)
                if current["function:priced-model"].rules[0].prices[0].price == Decimal(2):
                    break
                await sleep(0.01)
        second = HarnessBuilder().build(AgentSpec(), output_type=str, model=model)
        updated = await second.run("go", bindings=RunBindings.embedded())
        unchanged = await first.run("go", bindings=RunBindings.embedded())
        assert initial.usage.cost is not None and initial.usage.cost > 0
        assert updated.usage.cost == initial.usage.cost * 2
        assert unchanged.usage.cost == initial.usage.cost
        assert isinstance(updated.usage_records[0], ModelUsageRecord)
        assert updated.usage_records[0].pricing_revision == _capability(second).revision
        assert updated.usage_records[0].pricing_status == "applied"
    finally:
        updater.stop()
        for thread in threading.enumerate():
            if thread.name == "genai_prices:update":
                await to_thread.run_sync(thread.join, 5)
                assert not thread.is_alive()


def test_custom_snapshot_is_not_mislabeled_as_bundled() -> None:
    custom = _snapshot()
    custom.from_auto_update = False
    set_custom_snapshot(custom)
    entry = pricing.get_current_pricing_catalog()["openai:gpt-5.5"]
    assert ":custom:" in entry.source_revision
    assert entry.rules[0].prices[0].price == Decimal(1)


def test_matching_changes_are_part_of_the_snapshot_revision() -> None:
    first_source = _snapshot()
    first_source.providers[0].models[0].match = ClauseEquals(equals="preview-ref")
    set_custom_snapshot(first_source)
    first = pricing.get_current_pricing_catalog()
    second_source = deepcopy(first_source)
    second_source.providers[0].models[0].match = ClauseEquals(equals="replacement-ref")
    set_custom_snapshot(second_source)
    second = pricing.get_current_pricing_catalog()
    assert first.resolve("preview-ref", provider="openai") == first["openai:gpt-5.5"]
    assert second.resolve("replacement-ref", provider="openai") == second["openai:gpt-5.5"]
    assert second.revision != first.revision


def test_refresh_retains_packaged_tiers_but_explicit_replacement_can_remove_them() -> None:
    from dataclasses import replace

    bundled = pricing.get_default_pricing_catalog()
    set_custom_snapshot(_snapshot("3"))
    current = pricing.get_current_pricing_catalog()
    entry = current["openai:gpt-5.5"]
    policy = CatalogModelCostCapability(catalog=current)
    assert policy.quote(_input()).cost_usd == Decimal(3)
    flex = replace(_input(), service_tier="flex", usage=RequestUsage(input_tokens=1000))
    assert policy.quote(flex).cost_usd == Decimal("0.0025")
    assert ":tiers:" in entry.source_revision
    assert bundled[entry.key].rules[0].prices[0].price == Decimal(5)
    standard_only = entry.model_copy(update={"rules": (entry.rules[0],)})
    explicit = CatalogModelCostCapability(catalog=current, pricing_updates={entry.key: standard_only})
    assert explicit.quote(flex) is None
    assert policy.quote(flex).cost_usd == Decimal("0.0025")
