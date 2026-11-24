---
title: Usage, limits, and pricing
description: Enforce request and token budgets on Harness runs, accumulate usage across turns and subagents, and calculate costs with customizable pricing catalogs.
---

Set request and token budgets via `AgentSpec.usage_limits` (`pydantic_ai.usage.UsageLimits`), read attributed usage and USD cost on `result.usage` / `result.usage_records`, and stream snapshots to a Host `usage_reporter`.

## Set budgets and report usage

```python title="usage_demo.py"
import asyncio
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.usage import UsageSnapshot, select_usage_snapshot
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import UsageLimits


class LatestUsage:
    def __init__(self) -> None:
        self.snapshots: dict[str, UsageSnapshot] = {}

    async def report(self, snapshot: UsageSnapshot) -> None:
        prev = self.snapshots.get(snapshot.usage_id)
        self.snapshots[snapshot.usage_id] = (
            snapshot.model_copy(deep=True) if prev is None else select_usage_snapshot(prev, snapshot)
        )


async def main() -> None:
    reporter = LatestUsage()
    executable = HarnessBuilder().build(
        AgentSpec(
            usage_limits=UsageLimits(request_limit=10, total_tokens_limit=50_000),
            retries={"tools": 2, "output": 1},
        ),
        output_type=str,
        model=TestModel(),
    )
    first = await executable.run("Start work", bindings=RunBindings.embedded(usage_reporter=reporter))
    continued = await executable.run(
        "Continue same accounting scope",
        previous_state=first.state,
        resume_usage=True,
        bindings=RunBindings.embedded(usage_reporter=reporter),
    )
    print(first.usage.requests, continued.usage.requests)


asyncio.run(main())
```

## Usage

- **`result.usage` / `stream.usage`:** Detached `RunUsageSummary` (requests, input/output/cache/audio tokens, tool calls, `Decimal` USD cost, and unknown-cost flags).
- **`result.usage_records`:** Per-call attributed records carrying `call_id` (matching [pre-dispatch checks](hosting.md#check-model-calls-before-dispatch)), `source` (primary model, `tool.review`, `files.media_understanding`, subagents), and pricing rule metadata.
- **Accounting scopes:** Ordinary `run()` / `stream()` calls start a fresh `usage_id` scope; pass `resume_usage=True` to continue the saved `a13n.usage` scope across Runs, or implement `UsageDeltaReporter.report_delta(delta)` for incremental storage.

## Pricing catalogs and custom overrides

`HarnessBuilder` attaches `CatalogModelCostCapability` by default using bundled + refreshed pricing (`get_current_pricing_catalog()` in `a13n_harness.pricing`, honoring served `service_tier` metadata).

- **Background catalog refresh:** Run `prices.update_in_background()` and `run_official_model_updates` (`a13n_harness.model_catalog_updates`) once in a long-lived Host process (`A13N_OFFICIAL_MODELS_AUTO_UPDATE=0` disables official updates).
- **Custom model rates:** Override specific `provider:model` entries at build time (or pass `NoModelCostCapability()` to disable local valuation):

```python
from decimal import Decimal
from a13n_harness.pricing import (
    CatalogModelCostCapability,
    ModelPriceRule,
    ModelPricingEntry,
    PriceComponent,
)

entry = ModelPricingEntry(
    provider="demo",
    model="text-model",
    source="host",
    source_revision="v1",
    rules=(
        ModelPriceRule(
            rule_id="standard",
            prices=(
                PriceComponent(price_key="input_mtok", price=Decimal("1")),
                PriceComponent(price_key="output_mtok", price=Decimal("2")),
            ),
        ),
    ),
)
costs = CatalogModelCostCapability(pricing_updates={entry.key: entry})
```
