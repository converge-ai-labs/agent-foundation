# Observation

The Harness exposes an opt-in OpenTelemetry observation layer. The Host owns SDK configuration, resources, sampling, processors, exporters, propagation, flush, and shutdown. The Harness never constructs an exporter or collector client.

`HarnessBuilder()` reads the bounded `A13N_HARNESS_*` policy variables directly from `os.environ` at construction time. It never opens `.env` or any other configuration file. A Host process, launcher, container runtime, or development command may load a file and export its values before the Harness starts. The defaults keep observation disabled. Pass `instrumentation=None` to disable observation explicitly regardless of the environment, or pass a `HarnessInstrumentation` value to override the environment with exact providers.

## Configure a generic OpenTelemetry Host

Install and configure the OpenTelemetry SDK in the executable Host, then pass the exact providers to the Harness:

```python
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

from a13n_harness import (
    HarnessBuilder,
    HarnessInstrumentation,
    HarnessTraceContent,
)

resource = Resource.create({"service.name": "agent-worker"})
tracer_provider = TracerProvider(resource=resource)
meter_provider = MeterProvider(resource=resource)

# Add Host-selected span processors, metric readers, and exporters here.

builder = HarnessBuilder(
    instrumentation=HarnessInstrumentation(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        trace_content=HarnessTraceContent.STANDARD,
    )
)
```

At least one provider is required when `HarnessInstrumentation` is present:

- tracer only enables trace-only observation;
- meter only enables metrics-only observation;
- both providers enable both signals;
- `instrumentation=None` disables both signals.

The Host must flush and shut down its providers at the process boundary. The Harness does not flush after each run.

## Select trace structure and content independently

Trace structure is intentionally binary:

| State   | Selected structure                                                                                                                |
| ------- | --------------------------------------------------------------------------------------------------------------------------------- |
| off     | No Harness-selected spans.                                                                                                        |
| enabled | `harness.run`, Pydantic AI Agent/model/tool/streaming/cancellation spans, and independently meaningful `harness.operation` spans. |

In the explicit Python API, a tracer provider enables the complete structure. The environment convention uses `off` and `verbose` for these two states. There is no summary or intermediate structural mode.

Content policy applies only to Pydantic AI instrumentation:

| Policy     | Normal text and tool content | Binary content | Serialized model request parameters |
| ---------- | ---------------------------- | -------------- | ----------------------------------- |
| `none`     | Omitted                      | Omitted        | Omitted                             |
| `standard` | Included                     | Omitted        | Omitted                             |
| `full`     | Included                     | Included       | Included                            |

The content default is `standard`. While tracing is off, that value is a dormant policy and does not emit data or make metrics-only instrumentation invalid. Supplying a tracer provider enables complete structure with the selected content policy.

`none` means the Host does not opt into ordinary execution payloads. It is not a universal redaction boundary for fields emitted by upstream Pydantic AI instrumentation. Before enabling tracing, treat Agent descriptions, metadata, tool definitions and schema defaults, and upstream exception text as telemetry-visible. `standard` also cannot redact binary values nested inside arbitrary custom models or dataclasses.

## Enable observation and select a collector through environment variables

The default builder mode is `instrumentation="environment"`. It reads these Harness policy variables once and obtains the OpenTelemetry global providers already configured by the executable Host:

| Variable                     | Values                     | Default    |
| ---------------------------- | -------------------------- | ---------- |
| `A13N_HARNESS_TRACE_LEVEL`   | `off`, `verbose`           | `off`      |
| `A13N_HARNESS_TRACE_CONTENT` | `none`, `standard`, `full` | `standard` |
| `A13N_HARNESS_METRICS`       | `off`, `standard`          | `off`      |

Invalid values fail during `HarnessBuilder()` construction. The defaults are identical for development and production: trace off, standard content policy, metrics off.

Use the official OpenTelemetry Python distro and OTLP exporter to configure the SDK and collector target. The Harness does not reimplement their environment handling:

```bash
python -m pip install 'opentelemetry-distro[otlp]'

export A13N_HARNESS_TRACE_LEVEL=verbose
export A13N_HARNESS_TRACE_CONTENT=standard
export A13N_HARNESS_METRICS=standard

export OTEL_SERVICE_NAME=agent-worker
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318

opentelemetry-instrument python -m my_agent_host
```

`OTEL_EXPORTER_OTLP_ENDPOINT` selects a common collector endpoint; the official OTLP/HTTP exporters append `/v1/traces` and `/v1/metrics`. Signal-specific `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` and `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` take precedence when set. Standard `OTEL_*` variables also own resources, sampling, batching, headers, TLS, compression, timeout, and temporality.

The `opentelemetry-instrument` launcher configures global SDK providers before application code constructs `HarnessBuilder()`. `make dev` performs the same wrapping automatically when `.env` enables either Harness signal. An executable that configures providers in code can instead call `HarnessInstrumentation.from_environment(tracer_provider=..., meter_provider=...)`, or pass a fully explicit `HarnessInstrumentation` value. Explicit builder configuration wins over `A13N_HARNESS_*`; `instrumentation=None` is an explicit opt-out.

Use these safe process-environment defaults when no backend is selected:

```bash
export A13N_HARNESS_TRACE_LEVEL=off
export A13N_HARNESS_TRACE_CONTENT=standard
export A13N_HARNESS_METRICS=off
export OTEL_TRACES_EXPORTER=none
export OTEL_METRICS_EXPORTER=none
```

The later backend sections contain complete Logfire and Langfuse profiles. Put the selected values directly in the process environment, or let Host-owned deployment tooling export them. Do not add file loading to Harness code.

## Use one OpenTelemetry pipeline

Use OpenTelemetry APIs for Host-owned roots and custom lifecycle spans regardless of the selected backend. Backend SDKs configure providers, processors, exporters, and vendor-specific features; they do not own a second logical trace.

This gives the following behavior:

- with Logfire only, `logfire.configure()` installs the global OpenTelemetry trace and metric providers, so every Harness and Host OTel span is exported to Logfire;
- with Langfuse only, a generic OTLP provider or `LangfuseSpanProcessor` exports the same OTel trace to Langfuse;
- with both, one shared provider has both Logfire's processors and `LangfuseSpanProcessor`; do not create one Logfire root and another Langfuse root;
- Langfuse-specific APIs for scores, prompt management, or trace updates remain vendor-specific and are not reproduced automatically in Logfire.

A span created by the Langfuse Python SDK v3 is still an OpenTelemetry span. It reaches Logfire when both SDKs share Logfire's global provider, even if no Langfuse exporter is configured. However, generic Host and Harness spans should use `opentelemetry.trace` directly so backend selection remains portable.

## Recommended Logfire profile

Logfire is the recommended profile when one backend should receive both Harness traces and metrics. The Logfire SDK is built on OpenTelemetry and installs its trace and metric providers globally. It is not necessary to point a generic OTLP exporter at Logfire Cloud.

Install Logfire in the executable Host and set its write token:

```bash
python -m pip install logfire

export LOGFIRE_TOKEN=your-logfire-write-token
export LOGFIRE_SERVICE_NAME=agent-worker
export LOGFIRE_SEND_TO_LOGFIRE=if-token-present
export A13N_HARNESS_TRACE_LEVEL=verbose
export A13N_HARNESS_TRACE_CONTENT=standard
export A13N_HARNESS_METRICS=standard
```

Call a small backend bootstrap before constructing the builder. The conditional makes setting `LOGFIRE_TOKEN` the only deployment change needed to activate Logfire:

```python
import os

from opentelemetry import trace

from a13n_harness import (
    AgentSpec,
    HarnessBuilder,
)

if os.environ.get("LOGFIRE_TOKEN"):
    import logfire

    logfire.configure()

executable = HarnessBuilder().build(
    AgentSpec(model="openai-responses:gpt-5"),
    output_type=str,
)
host_tracer = trace.get_tracer("agent-host")

with host_tracer.start_as_current_span("host.work"):
    result = await executable.run("Complete the task")
```

Logfire still requires one `logfire.configure()` call; environment variables configure the call rather than replacing it. `LOGFIRE_SERVICE_NAME` supplies the OpenTelemetry `service.name` without a hard-coded Python value, and `OTEL_SERVICE_NAME` is its standard fallback alias. `LOGFIRE_TOKEN` selects the project, while `LOGFIRE_SEND_TO_LOGFIRE=if-token-present` keeps the same bootstrap safe in processes without a token.

`logfire.configure()` creates the global OpenTelemetry providers and configures Logfire's exporters. This is OpenTelemetry export managed by the Logfire SDK rather than a separately configured generic OTLP endpoint. The default `HarnessBuilder()` then selects those exact providers from the global OpenTelemetry registry. Automatic vendor bootstrap belongs at the executable Host boundary; the Harness only auto-selects its environment policy and the already configured global OTel providers. Do not call `logfire.instrument_pydantic_ai()`; Harness already owns the one Pydantic AI `Instrumentation` capability.

For an alternative OTLP backend instead of Logfire Cloud, configure Logfire with `send_to_logfire=False` and use the standard `OTEL_EXPORTER_OTLP_*` variables. See the [Logfire alternative-backend guide](https://pydantic.dev/docs/logfire/guides/alternative-backends/) and [Logfire configuration reference](https://pydantic.dev/docs/logfire/manage/configuration/).

## Recommended Langfuse profile

Langfuse is the recommended LLM trace backend. Its OTLP endpoint ingests traces, but it is not a Harness metrics backend. Keep `A13N_HARNESS_METRICS=off` and `OTEL_METRICS_EXPORTER=none` unless a separate metric-capable provider or collector is configured.

Configure Langfuse Cloud or a self-hosted instance through standard OTLP variables:

```bash
export LANGFUSE_PUBLIC_KEY=lf_pk_...
export LANGFUSE_SECRET_KEY=lf_sk_...
export LANGFUSE_BASE_URL=https://cloud.langfuse.com

export A13N_HARNESS_TRACE_LEVEL=verbose
export A13N_HARNESS_TRACE_CONTENT=standard
export A13N_HARNESS_METRICS=off
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=none
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT="$LANGFUSE_BASE_URL/api/public/otel"
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic $(printf '%s' "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" | base64 | tr -d '\n'),x-langfuse-ingestion-version=4"

opentelemetry-instrument python -m my_agent_host
```

Use `https://us.cloud.langfuse.com`, `https://jp.cloud.langfuse.com`, or the selected regional URL when the project is not in the default EU region. The endpoint for the repository's local stack is `http://127.0.0.1:3000/api/public/otel`.

Direct OTLP export sends `harness.run`, Pydantic Agent/model/tool spans, and material `harness.operation` spans to one Langfuse trace. If no Host span is current, `harness.run` is the trace root. If the Host uses the Langfuse Python SDK instead, attach `LangfuseSpanProcessor` to the existing shared provider rather than registering a second provider. Langfuse's default export filter is LLM-focused, so explicitly include the `a13n-harness` and any Host-root instrumentation scopes when filtering; otherwise those structural spans may be omitted.

Langfuse v4 requires trace-level grouping fields on every descendant span for reliable filtering and aggregation. A direct-OTLP Host should propagate only an explicit allowlist, using the Langfuse SDK's documented propagation context or OpenTelemetry baggage plus a bounded `SpanProcessor`:

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

The Host may copy the trusted conventional `user_id` identity claim to `langfuse.user.id`. Keep `a13n.user.id` as the vendor-neutral Harness field; do not add ambiguous `user.id` aliases in Harness code. `HarnessObservationContext` supplies an explicit trace name, product session, labels, and bounded scalar metadata when `harness.run` should be the root. A Host SpanProcessor can map `a13n.observation.name`, `a13n.observation.session.id`, `a13n.observation.labels`, and `a13n.observation.metadata.*` to the corresponding Langfuse fields and copy those allowlisted trace fields to descendants. Langfuse requires each flattened `langfuse.trace.metadata.*` OTLP attribute to be a string, so encode non-string Harness scalars deterministically without changing their original `a13n.*` values. Never propagate arbitrary baggage, all identity claims, `host_refs`, or `RunBindings.metadata`.

Langfuse v4 derives trace input and output from the root observation. `harness.run` deliberately remains content-safe, so a root Harness span carries no prompt or result. If trace-level input/output is required, create one meaningful Host root and set bounded JSON strings in `langfuse.observation.input` and `langfuse.observation.output`. Do not use deprecated `langfuse.trace.input` or `langfuse.trace.output`, and do not dump all run arguments, state, events, or usage records. Pydantic model/tool input and output continue to follow `HarnessTraceContent`.

For example, one Logfire-owned provider can export the same trace to both backends:

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

The repository includes an isolated Langfuse v4 development stack based on the official deployment composition:

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

Export this complete trace-only profile before launching an embedded Host. Repository development may place the same values in a root `.env`; `make dev` loads that file only at the Host launcher boundary.

```bash
export A13N_HARNESS_TRACE_LEVEL=verbose
export A13N_HARNESS_TRACE_CONTENT=standard
export A13N_HARNESS_METRICS=off
export OTEL_SERVICE_NAME=agent-worker
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=none
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:3000/api/public/otel
export OTEL_EXPORTER_OTLP_HEADERS='Authorization=Basic bGZfcGtfYWdlbnRfZm91bmRhdGlvbl9sb2NhbDpsZl9za19hZ2VudF9mb3VuZGF0aW9uX2xvY2Fs,x-langfuse-ingestion-version=4'

opentelemetry-instrument python -m my_agent_host
```

The trace appears in the local project after the Harness run completes. Langfuse does not receive metrics from this profile.

Stop the stack while preserving data, or remove it completely:

```bash
make langfuse-down
make langfuse-reset
```

The composition binds the Langfuse UI and media endpoint to loopback and does not reuse the repository's PostgreSQL or Redis services. Its credentials are for local development only. For production and high-availability deployments, follow the official [Langfuse self-hosting documentation](https://langfuse.com/self-hosting) rather than adapting this development composition.

## Add bounded context to the Harness root

Use a fresh `HarnessObservationContext` when `harness.run` should be the root and needs stable grouping fields without an otherwise redundant outer span:

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

The logical-run span receives only `a13n.observation.*` fields. Name and session ID are limited to 256 UTF-8 bytes. Labels are unique, limited to 16 entries and 64 UTF-8 bytes each. Metadata is limited to 16 validated keys and scalar string, boolean, signed 64-bit integer, or finite float values; strings are limited to 256 UTF-8 bytes. Invalid context fails before execution. It is not persisted in `HarnessState`, exposed to the model, or copied from `RunBindings.metadata`.

A vendor-specific Host processor may map these existing fields for presentation. For Langfuse, map name/session/labels/metadata to `langfuse.trace.name`, `langfuse.session.id`, `langfuse.trace.tags`, and `langfuse.trace.metadata.*`, and mark `harness.run` and `invoke_agent` as `agent` plus `execute_tool` as `tool`. For Logfire 4.41, map the root name to `logfire.msg` and labels to `logfire.tags`. Do not set `logfire.span_type` for normal spans, synthesize `logfire.level_num`, or write `logfire.metrics`; Logfire already infers normal-span type and error level and owns metric aggregation.

## Parent Harness runs through current context

The Harness uses only the current OpenTelemetry context. Create a Host span only when the application has a real enclosing work unit or needs Host-owned trace input/output. Make it current while entering and consuming the stream:

```python
host_tracer = tracer_provider.get_tracer("agent-host")

with host_tracer.start_as_current_span("host.work"):
    result = await executable.run("Complete the task")
```

`harness.run` becomes a child of `host.work`. The Harness does not accept a span, vendor observation, or trace ID argument.

For streaming, the Harness keeps its logical-run span open through cleanup and consumer backpressure, but detaches it before every public stream item is returned. Consumer-side spans therefore remain children of the Host context rather than becoming children of `harness.run`.

A Thread is correlation, not a trace. A later resume normally starts a new trace unless the Host activates a bounded distributed parent. For durable or independently scheduled work, prefer a new trace with a standard span link.

## Identity, lineage, usage, and cost fields

`harness.run` and its Pydantic Agent-attempt child carry a bounded trusted identity projection:

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

The Harness marks the exact active Pydantic model-request wrapper before applying this enrichment. Disabled Observation and metrics-only instrumentation have no eligible recording model span, so pricing fields never leak onto a Host root. The Harness does not create token aliases, another generation span, another usage metric, or a flattened usage ledger. Spans are telemetry, not billing authority.

Pydantic token categories are inclusive: input contains cache and input-audio tokens, output contains output-audio tokens, and reasoning detail may overlap output. Langfuse v4 splits cache counters but currently adds arbitrary audio/reasoning detail counters when deriving its displayed `usageDetails.total`. Preserve and inspect the individual categories, but do not treat that derived total as Pydantic `input_tokens + output_tokens` or as Harness accounting truth.

## Harness metric registry

A supplied meter provider enables these low-cardinality Harness instruments under the `a13n-harness` instrumentation scope:

| Instrument                        | Type          | Unit        | Attributes            |
| --------------------------------- | ------------- | ----------- | --------------------- |
| `a13n.harness.run.duration`       | Histogram     | `s`         | `a13n.run.outcome`    |
| `a13n.harness.run.active`         | UpDownCounter | `{run}`     | none                  |
| `a13n.harness.run.model_attempts` | Histogram     | `{attempt}` | `a13n.run.outcome`    |
| `a13n.harness.operation.duration` | Histogram     | `s`         | `a13n.operation.kind` |

Pydantic AI separately owns native token usage, cost, and time-to-first-chunk metrics. The Harness does not duplicate them. IDs, Agent names, failure codes, content, paths, and error text are never Harness metric dimensions.

## Instrumentation ownership

The Harness is the only Pydantic AI instrumentation owner for Agents it builds. It rejects authored, plugin, and run-scoped Pydantic `Instrumentation` capabilities and direct `InstrumentedModel` values. It also disables ambient `Agent.instrument_all()` state on every built Agent.

Do not call `logfire.instrument_pydantic_ai()` for a Harness-built Agent. Instead, configure Logfire or Langfuse as a Host profile over the same OpenTelemetry providers:

- let `harness.run` be the root when no real enclosing Host work unit is needed, otherwise create exactly one Host root span;
- supply the exact selected provider objects to `HarnessInstrumentation`;
- attach multiple processors or exporters to that shared provider when exporting to multiple backends;
- include the `a13n-harness` instrumentation scope in backend export filters;
- never create parallel Langfuse and Logfire roots for the same work unit.

The Harness has no Langfuse- or Logfire-specific runtime dependency.
