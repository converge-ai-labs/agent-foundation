---
title: Usage, limits, and pricing
description: Bound a Run with usage limits, read its usage records, and estimate cost with pricing policies.
---

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

`result.usage` and `stream.usage` return detached `RunUsageSummary` values: current model requests, token/audio counters, tool calls, provider receipts, Decimal USD cost, and explicit unknown-cost/incomplete coverage. They are not Pydantic AI's mutable `RunUsage`. `result.usage_records` contains current attributed contributions. Repeated observations of a provider-suspended generation refine one contribution, rather than adding another request or summing cumulative tokens.

Model records have an optional `call_id` correlating the contribution with its original Model invocation, including the Host's optional [pre-dispatch check](hosting.md#check-model-calls-before-dispatch). Usage reports use schema version `1`; a record without `call_id` loads with `call_id=None`. That value means unknown dispatch correlation, not proof that no provider call occurred. The ID is not an HTTP request ID or an exactly-once billing key, and it does not change existing record deduplication. Interrupted calls retain only actually observed usage; a check or allocated ID alone does not establish a charge.

Provider integrations can record stable non-model receipts through `AgentContext.record_provider_usage()`.

### Save and resume accounting

Usage is stored in the `a13n.usage` Capability namespace and exported with `HarnessState`. Every `run()` or `stream()` call resets accounting by default, including Runs that continue saved state, Runs on a forked Thread, and Runs that resume with human-in-the-loop answers. Steering within a running stream does not reset it. Previously persisted consumption is never deleted by reset.

To continue the same accounting scope explicitly:

```python
from a13n_harness.usage import UsageSnapshot

first = await executable.run("Start work")
assert first.state is not None
saved = UsageSnapshot.from_state(first.state)
assert saved is not None
continued = await executable.run(
    "Continue work", previous_state=first.state, resume_usage=True
)
```

The runtime Run ID changes, but the snapshot's `usage_id`, original contribution attribution and sequence continue. Missing or mismatched usage state fails explicitly. A provider-suspended generation requires this mode; resetting while continuing that generation is rejected before dispatch.

A Host can bind an async `UsageReporter.report(snapshot: UsageSnapshot)` through `RunBindings.usage_reporter`. Store only the latest snapshot per `usage_id`, using `select_usage_snapshot` to validate replacement, and commit each scope atomically. Do not sum cumulative snapshots or use display chunks as another ingestion path. The stream's `usage_report` display events carry bounded changed-record chunks; the reporter receives the complete detached state. Inline children inherit the reporter with independent scopes. A failed report stops execution and must be retried as delivery, not as model work.

For incremental storage, bind `UsageDeltaReporter.report_delta(delta: UsageDelta)` instead. `delta.scope` contains the scope identity, current sequence and tool-call count; `delta.records` contains only the latest values changed after `delta.after_sequence`. Commit these replacement contributions and scope progress atomically before returning. Accept retries and overlapping intervals after an uncertain commit, but reject gaps beyond stored progress. Harness retains unacknowledged changes for retry, independently of display delivery. If a reporter implements both methods, Harness calls `report_delta`. Complete checkpoints and result records remain available. Before `resume_usage=True`, ensure the restored accounting sequence and contributions are already durable in your storage.

When durable accounting is newer than the selected execution checkpoint, use `latest_snapshot.restore(checkpoint)` before explicit accounting resume. This overlays accounting only; it does not move message history or restore execution authority. Serialize each scope's writer. Independent worker attempts need distinct scopes so a late older worker cannot overwrite newer accounting. Snapshots are bounded to 10,000 records and 16 MiB; reaching capacity fails instead of silently dropping observations.

## Estimate model cost

Model-cost valuation is enabled by default. `HarnessBuilder` inserts `CatalogModelCostCapability`, which freezes the current valid pricing catalog for the built Agent. Without Host-enabled updates this is bundled `genai-prices` data plus Harness supplements. `get_default_pricing_catalog()` always reads that bundled baseline; `get_current_pricing_catalog()` additionally adopts successful upstream updates. Both return immutable catalogs without downloading anything. Read or export the current snapshot:

```python
from a13n_harness.pricing import get_current_pricing_catalog

pricing = get_current_pricing_catalog()
entry = pricing["openai:gpt-5.5"]
exported = pricing.model_dump(mode="json")
```

### Keep Prices Current in a Host

Pydantic AI exposes `prices.update_in_background()`. Start it once in your final application process, not during import or before forking worker processes. The following sketch uses your application's `serve()` function:

```python
from pydantic_ai import prices

async def main():
    with prices.update_in_background():
        await serve()
```

The upstream updater downloads immediately and then hourly. Startup need not wait for the first download: bundled prices are usable immediately, and failed downloads retain the last good data. Every later `HarnessBuilder.build()` automatically captures validated updates without restarting or clearing a cache. An already built executable keeps its old prices even when reused; rebuild it to adopt updates. The same rule keeps an active Run and its inline descendants stable.

In an async Host, capture the catalog off the event loop and pass it to `HarnessBuilder.build()`. The explicit snapshot is used for default pricing only; a custom model-cost Capability still wins:

```python
from anyio import to_thread
from a13n_harness import HarnessBuilder
from a13n_harness.pricing import get_current_pricing_catalog

catalog = await to_thread.run_sync(get_current_pricing_catalog)
executable = HarnessBuilder().build(definition, pricing_catalog=catalog)
```

Downloaded entries override packaged standard prices; missing entries keep bundled coverage. Because upstream `genai-prices` has no service-tier selector, packaged service-tier rules supplement refreshed standard entries. Their prices and provenance contribute to the effective revision. Explicit complete-entry overrides can still replace or remove those rules. Conversion failures retain the previous valid catalog. Identical downloaded pricing content keeps the same revision regardless of retrieval time. No price history or disk cache is created. Stopping the updater does not erase already downloaded prices; pass `get_default_pricing_catalog()` as `pricing_catalog` when a build must use bundled data regardless of other process activity.

### Price the Served Service Tier

Harness uses the **actual served tier** in `ModelResponse.provider_details`, not the request's `service_tier` setting. Pydantic AI exposes this for OpenAI Chat/Responses (including streaming) and Gemini Developer API. A priority request downgraded to `default` is priced at standard rates. Cost is calculated on each response before native usage accumulation, including inherited child and auxiliary policies.

`ModelPriceRule.service_tier` selects an exact tier; omitted values describe standard pricing. The last active matching rule wins within that tier, independently of date/time conditions. Missing tier metadata uses standard prices; `default`, `standard`, and `on_demand` may use an untiered rule. Other tiers require an explicit matching rule: there is no universal discount/premium multiplier. OpenAI's `fast` response spelling selects its `priority` tariff. `max_input_tokens` bounds a tariff when the provider has not published prices above a context limit. Token-length cliffs remain `PriceComponent.tiers`, a separate dimension.

Unknown or unsupported tiers decline Harness valuation rather than silently applying standard rates. Existing upstream cost, if any, remains available with its original cost source; otherwise cost is unknown, not zero. Malformed served-tier metadata reports pricing failure without failing the Run. Missing tier metadata does **not** prove standard serving.

The bundled token-price coverage was checked against official tables on **September 26, 2026**:

| Provider                                                                                                                   | Public rules included                                                                                                                                                                                                                                                      | Important limits                                                                                                                                                                                                                                                                                                                |
| -------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [OpenAI](https://developers.openai.com/api/docs/pricing)                                                                   | 25 models in the union of Flex and Fast tables: GPT-6 Astra/Sol/Luna; GPT-5.6 Sol/Terra/Luna; GPT-5.5/Pro; GPT-5.4/Mini/Nano/Pro; GPT-5.2; GPT-5.1; GPT-5/Mini/Nano; GPT-4.1/Mini/Nano; GPT-4o/2024-05-13/Mini; o3; o4-mini                                                | Each model gets only its published tiers. [Fast](https://developers.openai.com/api/docs/guides/fast-mode) also uses the `priority` spelling. Published long-context rates start above 272,000 input tokens. GPT-5.5 Fast, GPT-5.4 Fast, and GPT-5.5 Pro Flex decline above that boundary instead of inventing rates.            |
| [Gemini Developer API](https://ai.google.dev/gemini-api/docs/pricing)                                                      | Standard, [Flex](https://ai.google.dev/gemini-api/docs/flex-inference), and [Priority](https://ai.google.dev/gemini-api/docs/priority-inference) for Gemini 3.8/3.7/3.6 Flash, 3.5 Flash/Flash-Lite, 3.1 Flash-Lite/Pro Preview, 3 Flash Preview, 2.5 Pro/Flash/Flash-Lite | Literal cache and audio prices are retained, including unchanged Flex cache prices for several models. Pro context cliffs start above 200,000 tokens. The 3.6–3.8 Flash introductory rates change January 1, 2027.                                                                                                              |
| [Vertex AI](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/priority-paygo)                                     | No automatic tier tariff                                                                                                                                                                                                                                                   | Actual `traffic_type` is passed as a lower-case tier identifier (for example `on_demand_priority`). Developer API tariffs are not borrowed for Vertex traffic. Author endpoint-specific rules in a custom policy.                                                                                                               |
| [Anthropic](https://platform.claude.com/docs/en/api/service-tiers) and [Groq](https://console.groq.com/docs/service-tiers) | No inferred nonstandard tariff                                                                                                                                                                                                                                             | Anthropic priority and Groq performance are capacity contracts, not a universal token surcharge; Groq Flex has on-demand prices. Native upstream adapters do not currently expose their served tier. Anthropic Fast uses a separate served `speed` dimension, also unavailable here. Request settings are not billing evidence. |

These are token-cost estimates, not invoice parity: regional uplifts, capacity commitments, storage duration, grounding, other product fees, and negotiated prices are not derived from a service tier. Bundled tier prices change with package updates, not with the upstream standard-price downloader.

For selected-Model `TokenPricingCapability` policies (including saved Service Model pricing), add tier rules to that complete entry. Existing standard-only entries are not silently replaced with public catalog rates; they decline nonstandard tiers until explicitly configured. In Service, the Console standard-price editor preserves authored tier rules.

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

One custom `AbstractModelCostCapability` supplied through build-time `capabilities=` atomically replaces the default. More than one is a definition error. Use `NoModelCostCapability()` to explicitly preserve only provider or upstream-library cost without Harness valuation. Inline child Runs inherit the parent's selected policy so the shared usage tree is valued consistently; the same child definition uses its own build-time policy when executed independently.

Pricing failure or model lookup miss does not fail the Agent Run. Usage records identify the pricing status, catalog revision, selected rule, and actual cost source. Durable aggregation, reconciliation, negotiated discounts, billing, and exporter delivery remain Host concerns.

## Design boundary

- Pydantic AI owns native usage accumulation and limit checks.
- Harness attributes root, child, and provider work and captures a pricing policy for the executable.
- The Host owns durable aggregation, billing, negotiated prices, and whether to enable background catalog updates.

For metrics and traces rather than cost accounting, see [Observation](observation.md).
