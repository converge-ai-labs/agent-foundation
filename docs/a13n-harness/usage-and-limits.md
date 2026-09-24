# Usage, limits, and pricing

Use usage limits to bound a logical Run, usage records to understand its work, and pricing policies to estimate cost. These are different concerns: a request budget is enforcement, an observed cost is not an invoice, and a terminal result is not a durable billing record.

## Set a Run budget

```python
from a13n_harness import AgentSpec
from pydantic_ai.usage import UsageLimits

spec = AgentSpec(
    usage_limits=UsageLimits(request_limit=100, total_tokens_limit=200_000),
    retries={"tools": 2, "output": 1},
)
```

A Run may replace the complete `UsageLimits` value; it does not merge individual fields. Tool/output retries do not configure network retries or interrupted-model recovery. See [usage limits and retries](agents-and-runs.md#usage-limits-and-retries) for defaults, children, and overrides.

## Usage

`result.usage` and `stream.usage` use Pydantic AI's native `RunUsage`. `result.usage_records` additionally contains detached Harness attribution records for committed model requests and provider-reported usage.

Model records have an optional `call_id` correlating the committed response with its native model-handler invocation, including the Host's optional [pre-dispatch check](hosting.md#check-model-calls-before-dispatch). Usage reports remain at schema version `1`; older records without the field load with `call_id=None`. That value means unknown dispatch correlation, not proof that no provider call occurred. The ID is not an HTTP request ID or an exactly-once billing key, and it does not change existing record deduplication. Interrupted calls retain only actually observed usage; a check or allocated ID alone does not establish a charge.

Provider integrations can record stable non-model receipts through `AgentContext.record_provider_usage()`.

Model-cost valuation is enabled by default. `HarnessBuilder` inserts `CatalogModelCostCapability`, which freezes the current valid pricing catalog for the built Agent. Without Host-enabled updates this is bundled `genai-prices` data plus Harness supplements. `get_default_pricing_catalog()` always reads that bundled baseline; `get_current_pricing_catalog()` additionally adopts successful upstream updates. Both return immutable catalogs without downloading anything. Read or export the current snapshot:

```python
from a13n_harness.pricing import get_current_pricing_catalog

pricing = get_current_pricing_catalog()
entry = pricing["openai:gpt-5.5"]
exported = pricing.model_dump(mode="json")
```

### Keep Prices Current in a Host

Pydantic AI 2.40 or later exposes `prices.update_in_background()`. Start it once in your final application process, not during import or before forking. The following sketch uses your application's `serve()` function:

```python
from pydantic_ai import prices

async def main():
    with prices.update_in_background():
        await serve()
```

The upstream updater downloads immediately and then hourly. Startup need not wait for the first download: bundled prices are usable immediately, and failed downloads retain the last good data. Every later `HarnessBuilder.build()` automatically captures validated updates without restarting or clearing a cache. An already built executable keeps its old prices even when reused; rebuild it to adopt updates. The same rule keeps an active run and its inline descendants stable.

In an async Host, capture the catalog off the event loop and pass it to the builder. The explicit snapshot is used for default pricing only; a custom model-cost Capability still wins:

```python
from anyio import to_thread
from a13n_harness import HarnessBuilder
from a13n_harness.pricing import get_current_pricing_catalog

catalog = await to_thread.run_sync(get_current_pricing_catalog)
executable = HarnessBuilder().build(definition, pricing_catalog=catalog)
```

Downloaded entries override packaged supplements; missing entries keep bundled coverage. Conversion failures retain the previous valid catalog. Identical downloaded pricing content keeps the same revision regardless of retrieval time. No price history or disk cache is created. Stopping the updater does not erase already downloaded prices; pass `get_default_pricing_catalog()` as `pricing_catalog` when a build must use bundled data regardless of other process activity.

### Override Pricing

To replace prices, create complete `ModelPricingEntry` values and pass a shallow update dictionary. Each value replaces the entire entry at that `provider:model` key and wins over downloaded and bundled prices; nested fields are not merged:

```python
from a13n_harness import HarnessBuilder
from a13n_harness.pricing import CatalogModelCostCapability

costs = CatalogModelCostCapability(
    pricing_updates={replacement.key: replacement},
)
executable = HarnessBuilder().build(
    spec,
    output_type=str,
    capabilities=(costs,),
)
```

One custom `AbstractModelCostCapability` supplied through build-time `capabilities=` atomically replaces the default. More than one is a definition error. Use `NoModelCostCapability()` to explicitly preserve only provider or upstream-library cost without Harness valuation. Inline child runs inherit the parent's selected policy so the shared usage tree is valued consistently; the same child definition uses its own build-time policy when executed independently.

Pricing failure or model lookup miss does not fail the Agent run. Usage records identify the pricing status, catalog revision, selected rule, and actual cost source. Durable aggregation, reconciliation, negotiated discounts, billing, and exporter delivery remain Host concerns.

## Design boundary

- Pydantic AI owns native usage accumulation and limit checks.
- Harness attributes root, child, and provider work and captures a pricing policy for the executable.
- The Host owns durable aggregation, billing, negotiated prices, and whether to enable background catalog updates.

For metrics and traces rather than cost accounting, see [Observation](observation.md).
