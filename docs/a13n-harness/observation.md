---
title: Observation
description: Capture a Run locally, inspect its spans and metrics, then export through your Host's OpenTelemetry pipeline.
---

Start with one offline Run and an in-memory exporter. Once you can see its trace and duration metric, connect the same instrumentation to your collector, Logfire, or Langfuse. The Host owns provider setup and export; Harness instruments the Agent it builds.

Observation answers what happened during execution. Use the [Run result and saved state](hosting.md#events-and-streaming) to decide whether work completed and became durable.

## Configure OpenTelemetry in the Host

Use the Python environment from [Getting started](getting-started.md), then add the OpenTelemetry SDK:

```bash
uv pip install opentelemetry-sdk
```

Save this complete program as `observe.py`. It uses no network exporter or provider credentials:

```python title="observe.py"
import asyncio
from collections.abc import AsyncIterator

from a13n_harness import AgentSpec, HarnessBuilder, HarnessInstrumentation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel


async def respond(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str]:
    del messages, info
    yield "Observed locally."


async def main() -> None:
    resource = Resource.create({"service.name": "observation-demo"})
    spans = InMemorySpanExporter()
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    try:
        instrumentation = HarnessInstrumentation(
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
        )
        executable = HarnessBuilder(instrumentation=instrumentation).build(
            AgentSpec(), output_type=str, model=FunctionModel(stream_function=respond)
        )
        result = await executable.run("Check observation")
        print(result.output_or_raise())
        tracer_provider.force_flush()
        roots = [span for span in spans.get_finished_spans() if span.name == "harness.run"]
        assert len(roots) == 1
        assert roots[0].parent is None
        print("Logical Run spans:", len(roots))
        data = reader.get_metrics_data()
        assert data is not None
        names = {
            metric.name
            for resource_metrics in data.resource_metrics
            for scope_metrics in resource_metrics.scope_metrics
            for metric in scope_metrics.metrics
        }
        assert "a13n.harness.run.duration" in names
        print("Run duration metric recorded.")
    finally:
        meter_provider.shutdown()
        tracer_provider.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```

Run `uv run python observe.py`. Expect:

```text
Observed locally.
Logical Run spans: 1
Run duration metric recorded.
```

The program builds a Model, completes a Run, inspects an exported span and a metric, and shuts down both providers.

Choose signals by the provider objects passed to `HarnessInstrumentation`: tracer only for traces, meter only for metrics, or both for both. At least one is required. Pass `instrumentation=None` to `HarnessBuilder` to disable both explicitly. Flush and shut down providers at the Host process boundary; Harness does not flush them after every Run.

## Select trace structure and content independently

Choose the trace structure and payload policy separately. A tracer provider enables the complete structure: `harness.run`, preparation/finalization and Skill resolution, native Agent/Model/tool spans, and operations for recovery, delegation, handoff, compaction, and tool review. Without a tracer provider, Harness-selected tracing is off. Environment configuration calls these states `verbose` and `off`.

| Content policy | Normal text and tool content | Binary content | Serialized Model request parameters |
| -------------- | ---------------------------- | -------------- | ----------------------------------- |
| `none`         | Omitted                      | Omitted        | Omitted                             |
| `standard`     | Included                     | Omitted        | Omitted                             |
| `full`         | Included                     | Included       | Included                            |

`standard` is the default. To change the local example, import `HarnessTraceContent` from `a13n_harness` and add `trace_content=HarnessTraceContent.NONE` to its `HarnessInstrumentation(...)`. The Run span and metric remain; ordinary input/output bodies are omitted. The content setting is dormant when tracing is off.

Review telemetry-visible data before exporting it. `none` does not redact upstream Agent descriptions, metadata, tool definitions, schema defaults, or exception text. `standard` cannot redact binary values nested in arbitrary custom models or dataclasses.

When displaying collector content to users, `a13n_harness.observation.redact_json` returns a detached copy with authority-bearing fields such as `authorization`, `api_key`, `password`, `secret`, and `token` replaced. It also replaces Bearer credentials in strings while retaining usage counters such as `input_tokens`. Harness applies the same rule to extension event payloads.

## Enable observation and select a collector through environment variables

Use environment configuration when your launcher owns the telemetry backend. `HarnessBuilder()` defaults to `instrumentation="environment"`: it reads the following policy once from `os.environ` and selects the Host-configured global providers. It does not load `.env` files.

| Variable                     | Values                     | Default    |
| ---------------------------- | -------------------------- | ---------- |
| `A13N_HARNESS_TRACE_LEVEL`   | `off`, `verbose`           | `off`      |
| `A13N_HARNESS_TRACE_CONTENT` | `none`, `standard`, `full` | `standard` |
| `A13N_HARNESS_METRICS`       | `off`, `standard`          | `off`      |

Invalid values fail at builder construction. Development and production use the same defaults. An explicit `HarnessInstrumentation` overrides these variables; explicit `None` disables observation.

To export the offline Model through OTLP:

1. Install `opentelemetry-distro[otlp]` in the same Python environment.
2. Save `collector_demo.py` beside `observe.py`:

```python title="collector_demo.py"
import asyncio

from a13n_harness import AgentSpec, HarnessBuilder
from pydantic_ai.models.function import FunctionModel

from observe import respond


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=respond)
    )
    result = await executable.run("Check collector export")
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

3. In that environment's activated shell, export the following settings. Replace `http://otel-collector:4318` with your reachable collector. These commands use POSIX shell syntax.

```bash
export A13N_HARNESS_TRACE_LEVEL=verbose
export A13N_HARNESS_TRACE_CONTENT=standard
export A13N_HARNESS_METRICS=standard
export OTEL_SERVICE_NAME=agent-worker
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=none
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318

opentelemetry-instrument python collector_demo.py
```

4. Find service `agent-worker` in the collector's backend and inspect a completed `harness.run`. The console answer proves Model execution; the backend span proves export. Check exporter errors separately if the answer appears but the trace does not.

The launcher initializes global SDK providers before application code and owns their process lifecycle. OTLP/HTTP appends `/v1/traces` and `/v1/metrics` to the common endpoint. Signal-specific `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` and `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` take precedence. Standard `OTEL_*` variables configure resources, sampling, batching, headers, TLS, compression, timeout, and temporality.

For code-owned providers with environment-selected signals, use `HarnessInstrumentation.from_environment(tracer_provider=..., meter_provider=...)`. `make harness-dev` loads `dev/harness/.env` explicitly before its OTel launcher. Other Hosts must arrange their own file loading.

To switch off a launcher profile, set both Harness signals to `off` and `OTEL_TRACES_EXPORTER`, `OTEL_METRICS_EXPORTER`, and `OTEL_LOGS_EXPORTER` to `none`. Choose one of the backend profiles below instead of stacking independent provider bootstraps.

## Preparation, skill-resolution, and cleanup spans

Read a trace from the outer Run inward:

| Span                          | What to inspect                                            |
| ----------------------------- | ---------------------------------------------------------- |
| `harness.run`                 | Logical Run lifetime and outcome.                          |
| `harness.prepare`             | Environment entry, input preparation, and plugin binding.  |
| `harness.skills.resolve`      | Actual Skill catalog selection.                            |
| Native Agent/Model/tool spans | Model attempts, requests, and tool execution.              |
| `harness.operation`           | Recovery, delegation, handoff, compaction, or tool review. |
| `harness.finalize`            | Resource cleanup and state export.                         |

For preparation or cleanup failures, inspect the last `a13n.phase.step` and error classification. Phase output reports the local result: selection counts, state availability, cleanup failures, recovery backoff versus retry-input preparation, or before/after compaction message counts. Unknown Skill selections are rejections, not successful empty catalogs. These fields have filterable metadata aliases. Output bodies follow the Run content policy and bounds; `none` retains structural metadata. Phase spans add no new metrics, and Host checkpoint saving remains outside Harness.

The Run's `a13n.skills.available` and `a13n.skills.accessed` lists show offered Skills and successful reads through supported file tools. Each list holds at most 16 names, with counts and truncation diagnostics. Skill resolution output similarly includes up to 16 selected name/source pairs. `access_count` includes repeat reads; a partial read counts as access. Use the native file-tool span for the exact Skill and source. These observations do not infer shell reads, full loading, compliance, or use from earlier history. Child Runs keep their own summaries. Judge the enclosing Run separately from a completed operation.

## Deployment environment

Add `deployment.environment.name` to the Host's OpenTelemetry resource to distinguish local, staging, and production traces. In `observe.py`, replace the resource construction with:

```python
resource = Resource.create({
    "service.name": "observation-demo",
    "deployment.environment.name": "local",
})
```

For the environment launcher, set `OTEL_RESOURCE_ATTRIBUTES=deployment.environment.name=local`, preserving any other attributes already configured. `dev/harness/.env.example` uses that setting; merge it into your private file without overwriting credentials, then restart `make harness-dev`. This label describes deployment, not a Harness execution Environment. Existing observations keep their old labels, and Harness does not modify a supplied provider's resource.

## Use one OpenTelemetry pipeline

Use one provider hierarchy for a work unit. Create generic Host spans through `HarnessInstrumentation.get_tracer()` or the OpenTelemetry API; select destinations through processors and exporters.

- **Logfire:** `logfire.configure()` installs global trace and metric providers.
- **Langfuse:** export through a generic OTLP provider or attach `LangfuseSpanProcessor` to an existing provider.
- **Both:** attach both exporters/processors to the same provider. Host and Harness spans share one trace.

An OpenTelemetry-native Langfuse SDK span also reaches Logfire when it uses Logfire's shared provider. Langfuse-specific scores, prompt management, and trace updates remain separate vendor APIs. Keep generic lifecycle code on OpenTelemetry so changing the backend does not change your application's trace structure.

## Recommended Logfire profile

Use this profile to send traces and metrics through the Logfire SDK. It installs the global OpenTelemetry providers; Harness selects them when the enabled environment policy is read.

Install Logfire in the same Python environment as `collector_demo.py`. Supply your project write token through the process environment:

```bash
uv pip install logfire

export LOGFIRE_TOKEN=your-logfire-write-token
export LOGFIRE_SERVICE_NAME=agent-worker
export LOGFIRE_SEND_TO_LOGFIRE=if-token-present
export A13N_HARNESS_TRACE_LEVEL=verbose
export A13N_HARNESS_TRACE_CONTENT=standard
export A13N_HARNESS_METRICS=standard
```

Save `logfire_demo.py` beside `collector_demo.py` and `observe.py`, then run `python logfire_demo.py` in the activated environment. This reuses the offline Model so you can test telemetry without a Model API key:

```python title="logfire_demo.py"
import asyncio
import logfire

from collector_demo import main

logfire.configure()
asyncio.run(main())
```

Find the Run under service `agent-worker` in your selected Logfire project. `logfire.configure()` initializes the backend before `main()` constructs its builder; environment variables configure that call. `LOGFIRE_SERVICE_NAME` supplies the OpenTelemetry `service.name` without a hard-coded Python value, and `OTEL_SERVICE_NAME` is its standard fallback alias. `LOGFIRE_TOKEN` selects the project, while `LOGFIRE_SEND_TO_LOGFIRE=if-token-present` allows the bootstrap to run without exporting to Logfire when the token is absent. The SDK owns exporter shutdown at process exit; long-lived Hosts should flush and shut down their providers at the application lifecycle boundary.

Call this bootstrap in the executable Host and leave Pydantic AI instrumentation to Harness; see [Instrumentation ownership](#instrumentation-ownership).

For an alternative OTLP backend instead of Logfire Cloud, configure Logfire with `send_to_logfire=False` and use the standard `OTEL_EXPORTER_OTLP_*` variables. See the [Logfire alternative-backend guide](https://pydantic.dev/docs/logfire/guides/alternative-backends/) and [Logfire configuration reference](https://pydantic.dev/docs/logfire/manage/configuration/).

## Recommended Langfuse profile

Langfuse is the recommended backend for LLM traces when Harness metrics are not needed in the same backend. Its OTLP endpoint ingests traces, but it is not a Harness metrics backend. Keep `A13N_HARNESS_METRICS=off` and `OTEL_METRICS_EXPORTER=none` unless a separate metric-capable provider or collector is configured.

Use `collector_demo.py` and the `opentelemetry-distro[otlp]` installation from the collector exercise. Supply your Langfuse project keys, choose the project region or self-hosted base URL, and run this trace-only profile in the activated POSIX shell:

```bash
export LANGFUSE_PUBLIC_KEY=lf_pk_...
export LANGFUSE_SECRET_KEY=lf_sk_...
export LANGFUSE_BASE_URL=https://cloud.langfuse.com

export A13N_HARNESS_TRACE_LEVEL=verbose
export A13N_HARNESS_TRACE_CONTENT=standard
export A13N_HARNESS_METRICS=off
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=none
export OTEL_LOGS_EXPORTER=none
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT="$LANGFUSE_BASE_URL/api/public/otel"
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic $(printf '%s' "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" | base64 | tr -d '\n'),x-langfuse-ingestion-version=4"

opentelemetry-instrument python collector_demo.py
```

Use `https://us.cloud.langfuse.com`, `https://jp.cloud.langfuse.com`, or the selected regional URL when the project is not in the default EU region. The endpoint for the repository's local stack is `http://127.0.0.1:3000/api/public/otel`.

Direct OTLP export sends `harness.run`, Pydantic Agent/model/tool spans, and `harness.operation` spans to one Langfuse trace. If no Host span is current, `harness.run` is the trace root. If the Host uses the Langfuse Python SDK instead, attach `LangfuseSpanProcessor` to the existing shared provider rather than registering a second provider. Langfuse's default export filter is LLM-focused, so explicitly include the `a13n-harness` and any Host-root instrumentation scopes when filtering; otherwise those structural spans may be omitted.

Langfuse v4 requires trace-level grouping fields on every descendant span for reliable filtering and aggregation. Harness-selected spans already carry the Harness grouping fields (see [Automatic grouping and filtering](#automatic-grouping-and-filtering)). For independently instrumented scopes and deployment fields such as version, release, and environment, a direct-OTLP Host propagates only an explicit allowlist, using the Langfuse SDK's documented propagation context or OpenTelemetry baggage plus a bounded `SpanProcessor`:

| Purpose          | OTLP attribute                            |
| ---------------- | ----------------------------------------- |
| Trace name       | `langfuse.trace.name`                     |
| User grouping    | `langfuse.user.id`                        |
| Product session  | `langfuse.session.id`                     |
| Tags             | `langfuse.trace.tags`                     |
| Metadata         | `langfuse.trace.metadata.<approved-key>`  |
| Version          | `langfuse.version`                        |
| Release          | `langfuse.release`                        |
| Environment      | `langfuse.environment`                    |
| Observation type | `langfuse.observation.type`               |
| Observation I/O  | `langfuse.observation.input` and `output` |

The Harness copies the trusted conventional `user_id` identity claim to `langfuse.user.id` and keeps `a13n.user.id` as the vendor-neutral Harness field; it adds no ambiguous `user.id` alias. `HarnessObservationContext` supplies an explicit trace name, product session, labels, and bounded scalar metadata when `harness.run` should be the root. The Harness maps `a13n.observation.name`, `a13n.observation.session.id`, `a13n.observation.labels`, and `a13n.observation.metadata.*` to the corresponding Langfuse fields and copies them to Harness-selected descendants; a Host SpanProcessor is not needed for these fields. Langfuse requires each flattened `langfuse.trace.metadata.*` OTLP attribute to be a string, so the Harness encodes non-string scalars deterministically and keeps their original types in the `a13n.*` fields. Never propagate arbitrary baggage, all identity claims, `host_refs`, or `RunBindings.metadata`.

Langfuse v4 derives trace input and output from the root observation. At `standard` or `full`, `harness.run` records the prepared input and final middleware-owned result in `langfuse.observation.input` and `langfuse.observation.output`, with neutral `a13n.input` / `a13n.output` equivalents. Each is capped at 8 KiB; native media is descriptive only. `none` omits bodies. Inspect the corresponding `a13n.input.capture` / `a13n.output.capture` and truncation fields when content is absent or partial. Do not use deprecated trace input/output aliases or dump all arguments, state, events, or usage records. A Host with its own meaningful root can use `HarnessInstrumentation.record_input()` and `record_output()` under the same policy.

To export to both backends, install the Langfuse SDK alongside Logfire. In `logfire_demo.py`, replace `logfire.configure()` with the following bootstrap, leaving `asyncio.run(main())` after it. Keep the Model and all Runs on this one provider:

```python
import os

import logfire
from langfuse.opentelemetry import LangfuseSpanProcessor
from langfuse.span_filter import is_default_export_span

structural_scopes = {"a13n-harness", "agent-host"}
langfuse_processor = LangfuseSpanProcessor(
    public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
    secret_key=os.environ["LANGFUSE_SECRET_KEY"],
    base_url=os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
    should_export_span=lambda span: (
        is_default_export_span(span)
        or (
            span.instrumentation_scope is not None
            and span.instrumentation_scope.name in structural_scopes
        )
    ),
)
logfire.configure(additional_span_processors=[langfuse_processor])
```

Set `LOGFIRE_SERVICE_NAME` or `OTEL_SERVICE_NAME` in the process environment, then construct `HarnessBuilder()` only after this configuration. Host roots created with `trace.get_tracer("agent-host")` flow to both processors, while Harness still owns Pydantic AI instrumentation exactly once.

See the [Langfuse native OpenTelemetry guide](https://langfuse.com/integrations/native/opentelemetry) and [existing OpenTelemetry setup guide](https://langfuse.com/faq/all/existing-otel-setup).

### Run Langfuse locally

For a local backend, install Docker with Compose and run the following from the repository root. This starts the machine-shared Langfuse v4 development stack:

```bash
make langfuse-up
```

Open <http://127.0.0.1:3000> and sign in with these local development credentials:

```text
Email: dev@agent-foundation.local
Password: agent-foundation-local
```

The stack pre-creates the `Agent Foundation Local` organization and project with these deterministic development-only API keys:

```text
Public key: lf_pk_agent_foundation_local
Secret key: lf_sk_agent_foundation_local
```

`make langfuse-up` uses the machine-shared stack and public test configuration in `dev/observability/langfuse.py`. It does not read Service settings or the root `.env`. The Harness and Harness UI development targets (`make harness-dev`, `make cli`, `make webui`) load their own `.env` files. The Service exports and queries traces through its own `telemetry` settings; see [Service logs, metrics and traces](../a13n-service/configuration.md#logs-metrics-and-traces).

For an **embedded Harness Host**, explicitly export the following trace-only profile instead. You may keep Host-specific values in a private `.env` and load it explicitly with `uv run --env-file .env ...`; `.env.harness.example` documents optional debugging settings. Harness UI uses its normal YAML configuration plus the process environment and does not implicitly load this file.

```bash
export A13N_HARNESS_TRACE_LEVEL=verbose
export A13N_HARNESS_TRACE_CONTENT=standard
export A13N_HARNESS_METRICS=off
export OTEL_SERVICE_NAME=agent-worker
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=none
export OTEL_LOGS_EXPORTER=none
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:3000/api/public/otel
export OTEL_EXPORTER_OTLP_HEADERS='Authorization=Basic bGZfcGtfYWdlbnRfZm91bmRhdGlvbl9sb2NhbDpsZl9za19hZ2VudF9mb3VuZGF0aW9uX2xvY2Fs,x-langfuse-ingestion-version=4'

opentelemetry-instrument python collector_demo.py
```

After export, inspect a completed `harness.run` in the local project. If only the console answer appears, check the exporter endpoint, headers, and backend readiness. This profile sends no metrics to Langfuse.

Stop the stack while preserving its data:

```bash
make langfuse-down
```

Run `make langfuse-reset` only when you intend to delete the shared development stack and its data. This affects other checkouts on the same machine.

The composition binds the Langfuse UI and media endpoint to loopback (ports 3000 and 3001 by default) and does not reuse Service's PostgreSQL or Redis. All checkouts on the machine share one Compose project and its volumes; Service resets preserve Langfuse data. Its credentials are for local development only. For production and high-availability deployments, follow the official [Langfuse self-hosting documentation](https://langfuse.com/self-hosting) rather than adapting this development composition.

## Add bounded context to the Harness root

To group Runs by an application conversation, add the following imports to `observe.py` and replace its `result = await executable.run(...)` line with this binding and call inside `main()`:

```python
from a13n_harness import (
    HarnessObservationContext,
    RunBindings,
)

bindings = RunBindings.embedded(
    observation=HarnessObservationContext(
        name="answer-question",
        session_id="conversation-42",
        labels=("interactive", "support"),
        metadata={"channel": "web", "experiment": "control"},
    )
)
result = await executable.run("Complete the task", bindings=bindings)
```

Run the program again and inspect `roots[0].attributes`. The logical-Run span receives the `a13n.observation.*` fields and their Langfuse aliases. Name and session ID are limited to 256 UTF-8 bytes. Labels are unique, limited to 16 entries and 64 UTF-8 bytes each. Metadata is limited to 16 validated keys and scalar string, boolean, signed 64-bit integer, or finite float values; strings are limited to 256 UTF-8 bytes. Invalid context fails before execution. It is not persisted in `HarnessState`, exposed to the model, or copied from `RunBindings.metadata`.

A vendor-specific Host processor may map these existing fields for presentation. For Langfuse, no mapping is needed: the Harness already writes `langfuse.trace.name`, `langfuse.session.id`, `langfuse.trace.tags`, and `langfuse.trace.metadata.*`, and marks `harness.run` and `invoke_agent` as `agent` and `execute_tool` as `tool`. For Logfire 4.41, map the root name to `logfire.msg` and labels to `logfire.tags`. Do not set `logfire.span_type` for normal spans, synthesize `logfire.level_num`, or write `logfire.metrics`; Logfire already infers normal-span type and error level and owns metric aggregation.

## Nest Harness Runs under the current OpenTelemetry context

When the application has a real enclosing operation, make its Host span current while entering and consuming Harness. In `observe.py`, replace the Run call with:

```python
host_tracer = instrumentation.get_tracer("agent-host")
with host_tracer.start_as_current_span("host.work"):
    result = await executable.run("Check observation")
```

Also replace `assert roots[0].parent is None` with `assert roots[0].parent is not None`. The Harness Run is now a child of `host.work`; no span or trace ID argument is needed.

During streaming, the logical Run span stays open through cleanup and consumer backpressure. Harness detaches it before returning each public stream item, so spans created by the consumer remain under the Host context.

Use Thread IDs to correlate conversation turns, not as trace IDs. A later resume normally starts a new trace. For independently scheduled or durable work, start a new trace with a standard span link; activate a distributed parent only for a bounded enclosing operation.

## Automatic grouping and filtering

Filter Harness-selected spans by the bounded context and identity fields, regardless of exporter. Harness writes vendor-neutral `a13n.*` fields and Langfuse aliases on the same spans, preserving native GenAI/Logfire attributes.

An explicit observation session groups Runs by your application value; otherwise a Run uses its Thread ID. Name, labels, and metadata map to `langfuse.trace.name`, `langfuse.session.id`, `langfuse.trace.tags`, and filterable `langfuse.observation.metadata.*`. Langfuse metadata aliases are strings; `a13n.observation.metadata.*` retains scalar types. A trusted conventional user claim supplies `langfuse.user.id` when present.

For Host-owned operations, `instrumentation.get_tracer("agent-host")` propagates bounded neutral correlation locally to selected descendants. Child values win. Missing identity claims are not inherited by a new Run, linked detached traces do not inherit parent metadata, and this local propagation does not create network baggage. Independently instrumented third-party spans need their own integration.

## Identity, lineage, usage, and cost fields

Use identity fields to find the right Run, then inspect request-level usage and pricing provenance. Keep telemetry queries separate from durable accounting.

`harness.run` and its Harness-selected descendants carry a bounded trusted identity projection:

- `a13n.agent.identity.issuer` and `a13n.agent.identity.subject`;
- conventional claims `a13n.agent.id` and `a13n.user.id`, when present;
- `a13n.agent.instance.id` and optional `a13n.agent.parent_instance.id`;
- optional `a13n.delegation.id` and `a13n.actor`.

Only `agent_id` and `user_id` are projected from `AgentIdentityRef.claims`. Arbitrary claims and `AgentInstanceContext.host_refs` are excluded. Each projected identity or lineage value must be UTF-8 encodable, is limited to 1024 encoded bytes, and may not contain NUL. Unsafe or over-limit values are omitted, not truncated, so telemetry cannot create a false ID collision or change execution.

Pydantic owns model-request usage fields, including input/output tokens, first-class cache counters, audio/reasoning detail counters, native provider or `genai-prices` cost, and model metrics. When the Harness model-cost Capability applies custom pricing, it writes the quote before Pydantic finalizes the active model-request span. The same span then contains numeric `gen_ai.usage.cost` plus bounded provenance:

- `a13n.usage.cost.source`;
- `a13n.usage.pricing.status`;
- optional `a13n.usage.pricing.revision`;
- optional `a13n.usage.pricing.rule.id`.

The Harness marks the exact active Pydantic model-request wrapper before applying this enrichment. Disabled observation and metrics-only instrumentation have no eligible recording model span, so pricing fields never leak onto a Host root. The Harness does not create token aliases, another generation span, another usage metric, or a flattened usage ledger. Spans are telemetry, not billing authority.

Pricing provenance also appears as filterable `usage_cost_source`, `usage_pricing_status`, `usage_pricing_revision` and `usage_pricing_rule_id` observation metadata when available. The current quote interface supplies a monetary total, so no input/output cost split is invented. Native Pydantic `operation.cost` estimates and cost metrics can use a different pricing source; this integration does not patch upstream finalization to force equality with a Harness quote. Inspect provenance when comparing backends.

Pydantic token categories are inclusive: input contains cache and input-audio tokens, output contains output-audio tokens, and reasoning detail may overlap output. Langfuse v4 splits cache counters but currently adds arbitrary audio/reasoning detail counters when deriving its displayed `usageDetails.total`. Preserve and inspect the individual categories, but do not treat that derived total as Pydantic `input_tokens + output_tokens` or as Harness accounting truth.

## Harness metric registry

The local program reads these instruments through `InMemoryMetricReader`. In a deployed Host, attach a metric reader/exporter to the supplied meter provider and query the `a13n-harness` scope:

| Instrument                        | Type          | Unit        | Attributes            |
| --------------------------------- | ------------- | ----------- | --------------------- |
| `a13n.harness.run.duration`       | Histogram     | `s`         | `a13n.run.outcome`    |
| `a13n.harness.run.active`         | UpDownCounter | `{run}`     | none                  |
| `a13n.harness.run.model_attempts` | Histogram     | `{attempt}` | `a13n.run.outcome`    |
| `a13n.harness.operation.duration` | Histogram     | `s`         | `a13n.operation.kind` |

The duration histograms advise bucket boundaries in seconds, from 1 s to 1 h for Runs and from 5 ms to 5 min for operations, instead of the OpenTelemetry defaults sized for milliseconds; a Host's metric views can replace them.

Pydantic AI separately owns native token usage, cost, and time-to-first-chunk metrics. The Harness does not duplicate them. IDs, Agent names, failure codes, content, paths, and error text are never Harness metric dimensions.

## Instrumentation ownership

Keep one instrumentation owner per Harness-built Agent:

1. Configure the Host's OpenTelemetry providers before building.
2. Let Harness create native Pydantic AI instrumentation for its Agents.
3. Add backend processors/exporters to those providers and include the `a13n-harness` scope in export filters.
4. Create one Host root only when there is a meaningful enclosing operation.
5. Flush and shut down at the Host lifecycle boundary.

Do not add `logfire.instrument_pydantic_ai()`, a Pydantic `Instrumentation` Capability, or an `InstrumentedModel` around the same Agent. Harness rejects authored, plugin, and Run-scoped duplicate instrumentation and disables ambient `Agent.instrument_all()` state on built Agents. Logfire and Langfuse remain optional Host dependencies.

If observation is missing, check in order: selected signal, provider initialization before builder construction, processor/reader attachment, exporter endpoint and credentials, backend scope filters, and shutdown/flush. Return to the in-memory example to distinguish Harness instrumentation from remote export problems.
