---
title: Usage, limits, and pricing
description: Run an offline accounting example, choose when usage resets, and configure cost estimates.
---

Set a budget before running an Agent, inspect its observed usage, then decide what your Host must persist. The example below shows a new accounting scope and an explicitly continued scope using the same saved conversation.

## Set a Run budget

Use the environment from [Getting started](getting-started.md). Save this as `usage_demo.py` and run `uv run --locked python usage_demo.py` from the checkout root. It uses an offline Model and an in-memory reporter; no credentials are needed.

```python
import asyncio
from collections.abc import AsyncIterator

from a13n_harness import AgentSpec, HarnessBuilder, HarnessState, RunBindings
from a13n_harness.usage import UsageSnapshot, select_usage_snapshot
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import UsageLimits


async def respond(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str]:
    yield "done"


class LatestUsage:
    """In-memory replacement store for this single-writer example."""

    def __init__(self) -> None:
        self.snapshots: dict[str, UsageSnapshot] = {}

    async def report(self, snapshot: UsageSnapshot) -> None:
        previous = self.snapshots.get(snapshot.usage_id)
        self.snapshots[snapshot.usage_id] = (
            snapshot.model_copy(deep=True)
            if previous is None
            else select_usage_snapshot(previous, snapshot)
        )


async def main() -> None:
    reporter = LatestUsage()
    executable = HarnessBuilder().build(
        AgentSpec(
            usage_limits=UsageLimits(request_limit=3, total_tokens_limit=10_000),
            retries={"tools": 2, "output": 1},
        ),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )
    first = await executable.run(
        "Start work", bindings=RunBindings.embedded(usage_reporter=reporter)
    )
    assert first.output_or_raise() == "done"
    assert first.state is not None
    restored = HarnessState.model_validate_json(first.state.model_dump_json())
    initial = UsageSnapshot.from_state(restored)
    assert initial is not None

    fresh = await executable.run(
        "A new accounting scope", previous_state=restored,
        bindings=RunBindings.embedded(usage_reporter=reporter),
    )
    continued = await executable.run(
        "Continue the original accounting scope", previous_state=restored,
        resume_usage=True,
        bindings=RunBindings.embedded(usage_reporter=reporter),
    )
    assert fresh.output_or_raise() == continued.output_or_raise() == "done"
    assert (first.usage.requests, fresh.usage.requests, continued.usage.requests) == (1, 1, 2)
    assert continued.run_id != first.run_id
    assert reporter.snapshots[initial.usage_id].summary.requests == 2
    print("requests: first=1, fresh=1, continued=2")


if __name__ == "__main__":
    asyncio.run(main())
```

The output is `requests: first=1, fresh=1, continued=2`. Each call makes one Model request. The third call retains the first call's accounting, so its summary contains two requests.

`AgentSpec.usage_limits` supplies the budget. A Run's `usage_limits=` replaces that whole value; individual fields are not merged. Resumed accounting counts toward the selected budget. Tool and output retries control Agent-loop attempts; network retries and interrupted-model recovery have separate settings. See [usage limits and retries](agents-and-runs.md#usage-limits-and-retries) for defaults and child budgets.

## Usage

Read `result.usage` after a Run or `stream.usage` during streaming. Both return a detached `RunUsageSummary` with model requests, token/audio counters, tool calls, provider receipts, Decimal USD cost, and unknown-cost/incomplete-coverage fields. Read `result.usage_records` for the attributed contributions behind that summary.

A continued provider generation refines its existing contribution. Repeated cumulative observations do not add requests or tokens again. Provider integrations can add non-model receipts with `AgentContext.record_provider_usage()`; those receipts do not alter native model-token limits.

Each model record can carry `call_id` for correlation with the original invocation and the Host's [pre-dispatch check](hosting.md#check-model-calls-before-dispatch). Historical version-1 reports without this field load it as `None`. Use contribution identities for deduplication: a call ID is neither an HTTP request ID nor an exactly-once billing key. Interrupted calls retain observed usage; allocating an ID alone establishes no charge.

### Save and resume accounting

`HarnessState` exports accounting in the `a13n.usage` namespace. Choose the scope at the next call:

| Call                                          | Accounting behavior                                                                      |
| --------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `run()` or `stream()` with default settings   | Start a fresh scope, including saved-state continuation, forks, and human-answer resumes |
| `resume_usage=True` with matching saved usage | Continue its `usage_id`, contribution ownership, and sequence under a new runtime Run ID |
| Steering an active stream                     | Keep the active scope                                                                    |

The example's reporter stores one latest snapshot per `usage_id`. `select_usage_snapshot` validates replacement and preserves the newer compatible value. Production storage should commit that replacement atomically with one writer per scope. Resetting starts another scope; it does not delete previously persisted consumption. Missing or mismatched accounting fails explicit resume. Continuing a provider-suspended generation requires `resume_usage=True`.

For incremental storage, implement `UsageDeltaReporter.report_delta(delta)`. `delta.scope` carries identity, sequence, and tool-call count; `delta.records` carries the latest changed contributions after `delta.after_sequence`. Replace contributions and commit progress together. Accept retries and overlapping intervals after an uncertain commit; reject gaps beyond stored progress. When both reporter methods exist, Harness chooses `report_delta`. Complete checkpoints and result records remain available.

Reporter delivery and display delivery are separate. A reporter receives detached accounting; `usage_report` display events contain bounded changed-record chunks. Consume one accounting ingestion path, rather than adding snapshots or display chunks as charges. Inline children inherit the reporter with independent scopes. Report failure stops execution and retains pending facts: retry delivery, not the Model work.

Before explicit accounting resume, make the restored sequence and contributions durable. If storage is newer than the execution checkpoint, apply `latest_snapshot.restore(checkpoint)` first. This overlays accounting while retaining that checkpoint's message history and current execution-authority requirements. Use separate scopes for independent worker attempts. Each snapshot is bounded to 10,000 records and 16 MiB; capacity exhaustion fails rather than dropping observations.

## Estimate model cost

Every built Agent has one model-cost policy. By default, `HarnessBuilder` inserts `CatalogModelCostCapability` with an immutable pricing catalog. Inspect the catalog installed in your process:

```python
from a13n_harness.pricing import get_current_pricing_catalog

pricing = get_current_pricing_catalog()
print(pricing.revision)
print(list(pricing)[:5])
exported = pricing.model_dump(mode="json")
```

`get_default_pricing_catalog()` returns bundled `genai-prices` data plus packaged Harness supplements. `get_current_pricing_catalog()` also adopts successful process-local updates. Neither function downloads data. The export includes complete entries, rule conditions, source metadata, and a content revision; use it to inspect supported models and tariffs instead of maintaining a second model-price list.

### Keep Prices Current in a Host

Start updates once in the final application process. This helper accepts your async serving function and stops both update loops when serving ends:

```python
from collections.abc import Awaitable, Callable

import anyio
from pydantic_ai import prices
from a13n_harness.model_catalog_updates import run_official_model_updates


async def serve_with_price_updates(serve: Callable[[], Awaitable[None]]) -> None:
    updater = prices.update_in_background()
    try:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(run_official_model_updates)
            try:
                await serve()
            finally:
                tasks.cancel_scope.cancel()
    finally:
        updater.stop()
```

Call `await serve_with_price_updates(serve)` from your application entry point, where `serve` owns your server lifetime. Calling the helper enables downloads. Keep it out of imports and pre-fork initialization. Harness UI and Service already manage the official-model update loop.

The upstream updater downloads immediately, then hourly. The official loop refreshes the repository's shared model-facts/pricing document with startup jitter, hourly scheduling, and retry backoff. It validates the whole document before replacing both views. Process-wide scheduling avoids one download per Agent. Failures retain the last valid data, with bundled data available at startup. `A13N_OFFICIAL_MODELS_AUTO_UPDATE=0` disables new official loops independently of upstream price updates.

New builds capture refreshed prices. Existing executables, active Runs, and inline descendants keep their selected policy. In an async Host, capture the catalog off the event loop:

```python
from anyio import to_thread
from a13n_harness import AgentDefinition, HarnessBuilder
from a13n_harness.pricing import get_current_pricing_catalog


async def build_with_current_prices(definition: AgentDefinition):
    catalog = await to_thread.run_sync(get_current_pricing_catalog)
    return HarnessBuilder().build(definition, pricing_catalog=catalog)
```

Pass a complete definition, including its Model and output type, to `build_with_current_prices`. Downloaded standard entries replace matching packaged prices; missing entries retain bundled coverage. Official service-tier rules supplement upstream standard prices because the upstream catalog has no tier selector. Explicit complete-entry overrides take precedence over both.

Catalog conversion failure keeps the last valid catalog. Equal effective content keeps the same revision regardless of download time. Updates create no disk cache or price history and do not rewrite saved Model settings. Stopping an updater leaves already published process-local data available. For a reproducible bundled baseline, pass `get_default_pricing_catalog()` as `pricing_catalog=`. An explicit custom cost Capability still wins over that build argument.

### Price the Served Service Tier

Harness prices each response before native usage accumulation using the actual served tier in `ModelResponse.provider_details`. A requested `service_tier` does not select a tariff. For example, a priority request served as `default` uses standard pricing.

| Rule field or response value                            | Effect                                                           |
| ------------------------------------------------------- | ---------------------------------------------------------------- |
| `ModelPriceRule.service_tier`                           | Exact tier match; the last active matching rule wins             |
| No tier metadata, `default`, `standard`, or `on_demand` | May use an untiered standard rule                                |
| OpenAI response tier `fast`                             | Selects `priority` rules                                         |
| Another nonstandard tier                                | Requires an explicit rule; no universal discount or premium      |
| `max_input_tokens`                                      | Inclusive upper bound; larger requests decline that tariff       |
| `PriceComponent.tiers`                                  | Token-length price cliffs, independent of service-tier selection |

Unknown tiers decline Harness valuation and retain upstream cost if present; otherwise cost stays unknown. Malformed tier metadata records pricing failure without failing the Run. Missing metadata permits the legacy standard estimate but does not establish which tier served the request.

The catalog keeps endpoint-specific coverage. OpenAI and Gemini Developer API response metadata can select their published tier rules. Vertex `traffic_type` becomes a lower-case tier identifier, but the catalog does not borrow Developer API tier tariffs for Vertex. Supply an endpoint-specific policy for such traffic. Request-only capacity or speed preferences do not establish a served token tariff.

Read each installed entry for context bounds, cache/audio prices, and dated rules. Token estimates omit invoice adjustments such as regional uplifts, commitments, storage duration, grounding fees, and negotiated discounts. Recurring weekday rules do not model public-holiday calendars. Official tier supplements refresh through package or official-document updates, independently of upstream standard prices.

For a selected-Model `TokenPricingCapability`, author tier rules in its complete entry. A saved standard-only policy keeps that coverage until you change it; it is not silently replaced by public tariffs. Service's standard-price editor preserves existing authored tier rules.

### Override Pricing

Provide a complete replacement entry for the response's `provider:model` key. This helper uses fictional rates of USD 1 and USD 2 per million input and output tokens; replace the identity and rates with your own policy:

```python
from decimal import Decimal

from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.pricing import (
    CatalogModelCostCapability, ModelPriceRule, ModelPricingEntry, PriceComponent,
)
from pydantic_ai.models import Model


def build_with_custom_prices(model: Model):
    replacement = ModelPricingEntry(
        provider="demo", model="text-model",
        source="host", source_revision="demo-v1",
        rules=(ModelPriceRule(
            rule_id="standard",
            prices=(
                PriceComponent(price_key="input_mtok", price=Decimal("1")),
                PriceComponent(price_key="output_mtok", price=Decimal("2")),
            ),
        ),),
    )
    costs = CatalogModelCostCapability(pricing_updates={replacement.key: replacement})
    return HarnessBuilder().build(
        AgentSpec(), output_type=str, model=model, capabilities=(costs,),
    )
```

Pass your configured native Model to `build_with_custom_prices`. The entry applies to responses identified as `demo:text-model`; other identities retain the catalog's coverage. `pricing_updates` replaces whole entries, including all tier rules, rather than merging nested fields.

One build-time `AbstractModelCostCapability` replaces the default policy. More than one is a definition error. `NoModelCostCapability()` preserves provider/upstream cost without Harness valuation. Inline children inherit the parent's policy; independent execution of the same child uses its own build-time policy.

A lookup miss or pricing failure leaves the Agent Run usable. Inspect each record's pricing status, revision, selected rule, and actual cost source. Unknown cost is distinct from zero.

## Design boundary

Pydantic AI checks native usage limits. Harness attributes model, child, and provider work and values it with the executable's frozen policy. The Host persists and reconciles those observations, applies commercial billing rules, and owns updater lifetimes. A terminal result is an accounting input; durable billing requires the Host's commit.

For metrics and traces, continue to [Observation](observation.md).
